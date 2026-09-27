import logging
from fastapi import APIRouter, Depends, BackgroundTasks, Response

from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm.llm_service import LlmService
from app.core.dependencies import get_llm_service

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])

@api_v1_router.post("/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    request: ChatCompletionRequest,
    # background_tasks: BackgroundTasks,
    response: Response,
    llmService: LlmService = Depends(get_llm_service)
) -> ChatCompletionResponse:
    """
    Handle chat completions with semantic caching.
    """
    logger.info("Routing request to LLM Gateway...")
    
    llm_response, is_cached = await llmService.generate_completion(request)

    llm_response.is_cached = is_cached
    if is_cached:
        response.headers["X-Cache"] = "HIT"
    else:
        response.headers["X-Cache"] = "MISS"

    return llm_response
