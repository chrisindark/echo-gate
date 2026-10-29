import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.constants import CACHE_PREFER_INSTANT, EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.error_classification import classify_provider_error
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.core.responses import BaseAPIResponse
from app.modules.chat.chat_estimate_service import chat_estimate_service
from app.modules.chat.chat_schema import (
    SSE_DONE_EVENT,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatEstimateRequest,
    ChatEstimateResponse,
    format_sse_event,
)
from app.modules.llm.llm_router_service import LlmRouterService


class CacheSavePayload(BaseModel):
    request: ChatCompletionRequest
    response: ChatCompletionResponse


logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@api_v1_router.post(
    "/completions",
    response_model=ChatCompletionResponse,
    # Auth runs before the quota guard so a bad key is rejected without
    # consuming a request from the rate-limit bucket. Anonymous access is an
    # intentional feature here: the optional guard authenticates a supplied
    # X-API-Key and lets a keyless request through as the "anonymous" tenant,
    # which then falls under the default quota limits.
    dependencies=[Depends(api_key_auth_optional_guard), Depends(llm_quota_guard)],
)
async def create_chat_completion(
    http_request: Request,
    request: ChatCompletionRequest,
    http_response: Response,
    llmRouterService: LlmRouterService = Depends(get_llm_router_service),
) -> ChatCompletionResponse | StreamingResponse:
    """
    Handle chat completions with semantic caching.
    """
    logger.info("Routing request to LLM Gateway...")

    if not request.user:
        # Fallback to the identity resolved from X-API-Key by the middleware.
        request.user = getattr(http_request.state, "user", None)
    request.user_id = getattr(http_request.state, "user_id", None)
    request.tenant_id = getattr(http_request.state, "tenant_id", None)
    request.session_id = request.session_id or http_request.headers.get(
        EchoGateHeaders.SESSION_ID
    )
    request.conversation_id = request.conversation_id or http_request.headers.get(
        EchoGateHeaders.CONVERSATION_ID
    )

    api_key = getattr(http_request.state, "api_key", None)

    budget_header = http_request.headers.get(EchoGateHeaders.TIMEOUT_BUDGET_MS)
    timeout_budget_ms: float | None = None
    if budget_header:
        try:
            timeout_budget_ms = float(budget_header.strip())
        except ValueError:
            pass

    if request.stream:
        # `X-Cache-Prefer: instant` lets a caller that only wants the answer skip
        # SSE parsing entirely: a cache hit is returned as one JSON body. A miss
        # falls through to the normal upstream stream, and the lookups that just
        # missed are not repeated.
        cache_prefer = (
            (http_request.headers.get(EchoGateHeaders.CACHE_PREFER) or "")
            .strip()
            .lower()
        )
        skip_cache_lookup = False
        if cache_prefer == CACHE_PREFER_INSTANT:
            instant_response = await llmRouterService.get_instant_cache_hit(
                request, api_key=api_key
            )
            if instant_response is not None:
                http_response.headers[EchoGateHeaders.CACHE] = "HIT"
                http_response.headers[EchoGateHeaders.GATEWAY_DEGRADATION] = (
                    getattr(instant_response, "_degradation_state", None) or "none"
                )
                return instant_response
            skip_cache_lookup = True

        if timeout_budget_ms is not None:
            stream_iter = llmRouterService.generate_stream(
                request,
                api_key=api_key,
                timeout_budget_ms=timeout_budget_ms,
                skip_cache_lookup=skip_cache_lookup,
            ).__aiter__()
        else:
            stream_iter = llmRouterService.generate_stream(
                request,
                api_key=api_key,
                skip_cache_lookup=skip_cache_lookup,
            ).__aiter__()

        first_chunk = None
        try:
            first_chunk = await stream_iter.__anext__()
        except StopAsyncIteration:
            pass

        async def sse_event_generator():
            try:
                if first_chunk is not None:
                    yield format_sse_event(first_chunk)
                async for chunk in stream_iter:
                    yield format_sse_event(chunk)
                yield SSE_DONE_EVENT
            except Exception as e:
                logger.error(f"Error during streaming completion: {e}")
                provider = request.service_name or "upstream"
                classified = classify_provider_error(e, provider)
                error_payload = {
                    "error": {
                        "message": classified["message"],
                        "type": "server_error"
                        if classified["status_code"] >= 500
                        else "invalid_request_error",
                        "code": classified["status_code"],
                        "error_code": classified["error_code"],
                        "provider": classified["provider"],
                    }
                }
                yield format_sse_event(error_payload)
                yield SSE_DONE_EVENT

        quota_status = getattr(http_request.state, "quota_status", None)
        stream_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            EchoGateHeaders.ACCEL_BUFFERING: "no",
            # The router tags the first chunk with the state that produced it
            # (cache, primary, or fallback) before any bytes are streamed.
            EchoGateHeaders.GATEWAY_DEGRADATION: getattr(
                first_chunk, "_degradation_state", None
            )
            or "none",
        }
        # A StreamingResponse replaces the injected Response, so the quota guard's
        # X-RateLimit-* headers have to be re-applied here.
        if hasattr(quota_status, "to_headers"):
            stream_headers.update(quota_status.to_headers())

        return StreamingResponse(
            sse_event_generator(),
            media_type="text/event-stream",
            headers=stream_headers,
        )

    if timeout_budget_ms is not None:
        llm_response = await llmRouterService.generate_completion(
            request, api_key=api_key, timeout_budget_ms=timeout_budget_ms
        )
    else:
        llm_response = await llmRouterService.generate_completion(
            request, api_key=api_key
        )
    degradation = getattr(llm_response, "_degradation_state", None) or "none"
    http_response.headers[EchoGateHeaders.GATEWAY_DEGRADATION] = degradation

    return llm_response


@api_v1_router.post(
    "/estimate",
    response_model=ChatEstimateResponse,
    # Same optional identity as the completions surface (anonymous is allowed),
    # but no quota guard: nothing is generated and no provider is called.
    dependencies=[Depends(api_key_auth_optional_guard)],
)
async def estimate_chat_cost(request: ChatEstimateRequest) -> ChatEstimateResponse:
    """Estimate prompt tokens and per-model dollar cost without calling a provider."""
    return chat_estimate_service.estimate(request)


@api_v1_router.post(
    "/completions/semantic-search",
    response_model=BaseAPIResponse[dict],
)
async def semantic_search(
    http_request: Request,
    request: ChatCompletionRequest,
    llmRouterService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint for semantic search only.
    """
    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = getattr(http_request.state, "user_id", None)
    request.tenant_id = getattr(http_request.state, "tenant_id", None)
    request.session_id = request.session_id or http_request.headers.get(
        EchoGateHeaders.SESSION_ID
    )
    request.conversation_id = request.conversation_id or http_request.headers.get(
        EchoGateHeaders.CONVERSATION_ID
    )

    result = await llmRouterService.evaluate_semantic_cache(request)

    if result.get("intent"):
        result["intent"] = result["intent"].value

    return BaseAPIResponse.success_response(result)


@api_v1_router.post(
    "/completions/exact-search",
    response_model=BaseAPIResponse[dict],
)
async def exact_search(
    http_request: Request,
    request: ChatCompletionRequest,
    llmRouterService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint for exact search only.
    """
    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = getattr(http_request.state, "user_id", None)
    request.tenant_id = getattr(http_request.state, "tenant_id", None)
    request.session_id = request.session_id or http_request.headers.get(
        EchoGateHeaders.SESSION_ID
    )
    request.conversation_id = request.conversation_id or http_request.headers.get(
        EchoGateHeaders.CONVERSATION_ID
    )

    payload, point_id = await llmRouterService.get_exact_match(request)
    return BaseAPIResponse.success_response({"payload": payload, "point_id": point_id})


@api_v1_router.post(
    "/completions/cache-save",
    response_model=BaseAPIResponse[dict],
)
async def cache_save(
    http_request: Request,
    payload: CacheSavePayload,
    llmRouterService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint to save cache directly.
    """
    request = payload.request
    response = payload.response

    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = getattr(http_request.state, "user_id", None)
    request.tenant_id = getattr(http_request.state, "tenant_id", None)
    request.session_id = request.session_id or http_request.headers.get(
        EchoGateHeaders.SESSION_ID
    )
    request.conversation_id = request.conversation_id or http_request.headers.get(
        EchoGateHeaders.CONVERSATION_ID
    )

    point_id, _ = await llmRouterService.save_to_qdrant_cache(
        request=request, response=response
    )
    return BaseAPIResponse.success_response({"point_id": point_id})
