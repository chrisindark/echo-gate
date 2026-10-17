import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_llm_router_service
from app.core.responses import BaseAPIResponse
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.conversation.conversation_schema import (
    ChatCompletionRequestWithConversation,
    ConversationCreate,
    ConversationResponse,
    ConversationWithMessagesResponse,
    MessageCreate,
)
from app.modules.conversation.conversation_service import ConversationService
from app.modules.llm.llm_router_service import LlmRouterService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations"])


@api_v1_router.post("", response_model=BaseAPIResponse[ConversationResponse])
def create_conversation(
    http_request: Request, request: ConversationCreate, db: Session = Depends(get_db)
):
    user_id = getattr(http_request.state, "user", "anonymous")
    svc = ConversationService(db)
    result = svc.create_conversation(user_id=user_id, data=request)
    return BaseAPIResponse.success_response(result)


@api_v1_router.get("", response_model=BaseAPIResponse[list[ConversationResponse]])
def get_conversations(
    http_request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    user_id = getattr(http_request.state, "user", "anonymous")
    svc = ConversationService(db)
    result = svc.get_user_conversations(user_id=user_id, skip=skip, limit=limit)
    return BaseAPIResponse.success_response(result)


@api_v1_router.get(
    "/{conversation_id}",
    response_model=BaseAPIResponse[ConversationWithMessagesResponse],
)
def get_conversation(
    http_request: Request, conversation_id: str, db: Session = Depends(get_db)
):
    user_id = getattr(http_request.state, "user", "anonymous")
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return BaseAPIResponse.success_response(conv)


@api_v1_router.post("/{conversation_id}/chat")
async def chat_in_conversation(
    http_request: Request,
    conversation_id: str,
    request: ChatCompletionRequestWithConversation,
    db: Session = Depends(get_db),
    llmService: LlmRouterService = Depends(get_llm_router_service),
):
    user_id = getattr(http_request.state, "user", "anonymous")
    svc = ConversationService(db)

    conv = svc.get_conversation(conversation_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Add user message
    svc.add_message(
        conversation_id, MessageCreate(role="user", content=request.message)
    )

    # Get history
    db_messages = svc.get_conversation_messages(conversation_id)
    history_messages = [
        ChatMessage(role=m.role, content=m.content) for m in db_messages
    ]

    # Build ChatCompletionRequest
    chat_req = ChatCompletionRequest(
        service_name=request.service_name,
        model=request.model,
        messages=history_messages,
        temperature=request.temperature,
        user=user_id,
    )

    # Get LLM response
    llm_response = await llmService.generate_completion(chat_req)

    # Extract text and save assistant message
    if llm_response.choices and len(llm_response.choices) > 0:
        assistant_content = llm_response.choices[0].message.content
        svc.add_message(
            conversation_id, MessageCreate(role="assistant", content=assistant_content)
        )

    return BaseAPIResponse.success_response(llm_response)
