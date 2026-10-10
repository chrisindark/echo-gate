import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.core.dependencies import get_llm_router_service
from app.core.guard import llm_quota_guard
from app.core.responses import BaseAPIResponse
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm.llm_router_service import LlmRouterService


class CacheSavePayload(BaseModel):
    request: ChatCompletionRequest
    response: ChatCompletionResponse


logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@api_v1_router.post(
    "/completions",
    response_model=ChatCompletionResponse,
    dependencies=[Depends(llm_quota_guard)],
)
async def create_chat_completion(
    http_request: Request,
    request: ChatCompletionRequest,
    # background_tasks: BackgroundTasks,
    response: Response,
    llmService: LlmRouterService = Depends(get_llm_router_service),
) -> ChatCompletionResponse:
    """
    Handle chat completions with semantic caching.
    """
    logger.info("Routing request to LLM Gateway...")

    if not request.user:
        # Fallback to user set by UserMiddleware or custom headers
        request.user = getattr(http_request.state, "user", None)
    request.user_id = request.user_id or http_request.headers.get("X-User-ID")
    request.tenant_id = request.tenant_id or http_request.headers.get("X-Tenant-ID")
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    api_key = getattr(http_request.state, "api_key", None)
    llm_response = await llmService.generate_completion(request, api_key=api_key)

    return llm_response


@api_v1_router.post(
    "/completions/semantic-search",
    response_model=BaseAPIResponse[dict],
)
async def semantic_search(
    http_request: Request,
    request: ChatCompletionRequest,
    llmService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint for semantic search only.
    """
    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = request.user_id or http_request.headers.get("X-User-ID")
    request.tenant_id = request.tenant_id or http_request.headers.get("X-Tenant-ID")
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    result = await llmService.evaluate_semantic_cache(request)

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
    llmService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint for exact search only.
    """
    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = request.user_id or http_request.headers.get("X-User-ID")
    request.tenant_id = request.tenant_id or http_request.headers.get("X-Tenant-ID")
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    payload, point_id = await llmService.get_exact_match(request)
    return BaseAPIResponse.success_response({"payload": payload, "point_id": point_id})


@api_v1_router.post(
    "/completions/cache-save",
    response_model=BaseAPIResponse[dict],
)
async def cache_save(
    http_request: Request,
    payload: CacheSavePayload,
    llmService: LlmRouterService = Depends(get_llm_router_service),
) -> dict:
    """
    Test endpoint to save cache directly.
    """
    request = payload.request
    response = payload.response

    if not request.user:
        request.user = getattr(http_request.state, "user", None)
    request.user_id = request.user_id or http_request.headers.get("X-User-ID")
    request.tenant_id = request.tenant_id or http_request.headers.get("X-Tenant-ID")
    request.session_id = request.session_id or http_request.headers.get("X-Session-ID")
    request.conversation_id = request.conversation_id or http_request.headers.get(
        "X-Conversation-ID"
    )

    point_id, _ = await llmService.save_to_qdrant_cache(
        request=request, response=response
    )
    return BaseAPIResponse.success_response({"point_id": point_id})
