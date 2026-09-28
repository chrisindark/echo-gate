import logging

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import get_llm_router_service
from app.core.guard import llm_quota_guard
from app.modules.chat.chat_schema import (ChatCompletionRequest,
                                          ChatCompletionResponse)
from app.modules.llm.llm_router_service import LlmRouterService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@api_v1_router.post("/completions", response_model=ChatCompletionResponse, dependencies=[Depends(llm_quota_guard)])
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
        # Fallback to user set by UserMiddleware
        request.user = getattr(http_request.state, "user", "anonymous")

    api_key = getattr(http_request.state, "api_key", None)
    llm_response = await llmService.generate_completion(request, api_key=api_key)

    return llm_response
