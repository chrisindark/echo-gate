import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.dependencies import get_llm_router_service
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.core.responses import BaseAPIResponse
from app.modules.chat.chat_schema import (
    SSE_DONE_EVENT,
    ChatCompletionRequest,
    ChatCompletionResponse,
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
    llmRouterService: LlmRouterService = Depends(get_llm_router_service),
) -> ChatCompletionResponse | StreamingResponse:
    """
    Handle chat completions with semantic caching.
    """
    logger.info("Routing request to LLM Gateway...")

    if not request.user:
        # Fallback to user set by UserMiddleware or custom headers
        request.user = getattr(http_request.state, "user", None)
    request.user_id = (
        request.user_id
        or getattr(http_request.state, "user_id", None)
        or http_request.headers.get("X-User-ID")
    )
    request.tenant_id = (
        request.tenant_id
        or getattr(http_request.state, "tenant_id", None)
        or http_request.headers.get("X-Tenant-ID")
    )
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    api_key = getattr(http_request.state, "api_key", None)

    if request.stream:
        stream_iter = llmRouterService.generate_stream(
            request, api_key=api_key
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
                error_payload = {
                    "error": {
                        "message": str(e),
                        "type": "server_error",
                        "code": 500,
                    }
                }
                yield format_sse_event(error_payload)
                yield SSE_DONE_EVENT

        return StreamingResponse(
            sse_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    llm_response = await llmRouterService.generate_completion(request, api_key=api_key)

    return llm_response


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
    request.user_id = (
        request.user_id
        or getattr(http_request.state, "user_id", None)
        or http_request.headers.get("X-User-ID")
    )
    request.tenant_id = (
        request.tenant_id
        or getattr(http_request.state, "tenant_id", None)
        or http_request.headers.get("X-Tenant-ID")
    )
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
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
    request.user_id = (
        request.user_id
        or getattr(http_request.state, "user_id", None)
        or http_request.headers.get("X-User-ID")
    )
    request.tenant_id = (
        request.tenant_id
        or getattr(http_request.state, "tenant_id", None)
        or http_request.headers.get("X-Tenant-ID")
    )
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
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
    request.user_id = (
        request.user_id
        or getattr(http_request.state, "user_id", None)
        or http_request.headers.get("X-User-ID")
    )
    request.tenant_id = (
        request.tenant_id
        or getattr(http_request.state, "tenant_id", None)
        or http_request.headers.get("X-Tenant-ID")
    )
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    point_id, _ = await llmRouterService.save_to_qdrant_cache(
        request=request, response=response
    )
    return BaseAPIResponse.success_response({"point_id": point_id})
