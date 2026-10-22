import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_llm_router_service
from app.core.guards import (
    api_key_auth_optional_guard,
    llm_quota_guard,
    resolve_caller_user,
)
from app.core.responses import BaseAPIResponse
from app.core.rls import ANONYMOUS_TENANT
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.conversation.conversation_schema import (
    ChatCompletionRequestWithConversation,
    ConversationCreate,
    ConversationResponse,
    ConversationWithMessagesResponse,
)
from app.modules.conversation.conversation_service import ConversationService
from app.modules.llm.llm_router_service import LlmRouterService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations"])


@api_v1_router.post(
    "",
    response_model=BaseAPIResponse[ConversationResponse],
    dependencies=[Depends(api_key_auth_optional_guard)],
)
def create_conversation(
    http_request: Request, request: ConversationCreate, db: Session = Depends(get_db)
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)
    result = svc.create_conversation(user_id=user_id, data=request, tenant_id=tenant_id)
    return BaseAPIResponse.success_response(result)


@api_v1_router.get(
    "",
    response_model=BaseAPIResponse[list[ConversationResponse]],
    dependencies=[Depends(api_key_auth_optional_guard)],
)
def get_conversations(
    http_request: Request,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)
    result = svc.get_user_conversations(
        user_id=user_id, tenant_id=tenant_id, skip=skip, limit=limit
    )
    return BaseAPIResponse.success_response(result)


@api_v1_router.get(
    "/{conversation_id}",
    response_model=BaseAPIResponse[ConversationWithMessagesResponse],
    dependencies=[Depends(api_key_auth_optional_guard)],
)
def get_conversation(
    http_request: Request, conversation_id: str, db: Session = Depends(get_db)
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id, user_id=user_id, tenant_id=tenant_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return BaseAPIResponse.success_response(conv)


@api_v1_router.post(
    "/{conversation_id}/chat",
    dependencies=[Depends(api_key_auth_optional_guard), Depends(llm_quota_guard)],
)
async def chat_in_conversation(
    http_request: Request,
    conversation_id: str,
    request: ChatCompletionRequestWithConversation,
    db: Session = Depends(get_db),
    llmService: LlmRouterService = Depends(get_llm_router_service),
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)

    conv = svc.get_conversation(conversation_id, user_id=user_id, tenant_id=tenant_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get conversation history from database
    db_messages = svc.get_conversation_messages(conversation_id)
    history_messages = [
        ChatMessage(role=m.role, content=m.content) for m in db_messages
    ]
    # Append the new user turn for completion
    history_messages.append(ChatMessage(role="user", content=request.message))

    # Build ChatCompletionRequest with identity and thread scope
    chat_req = ChatCompletionRequest(
        service_name=request.service_name,
        model=request.model,
        messages=history_messages,
        temperature=request.temperature,
        user=user_id,
        conversation_id=conversation_id,
    )
    chat_req.tenant_id = tenant_id
    chat_req.user_id = getattr(http_request.state, "user_id", None)

    api_key = getattr(http_request.state, "api_key", None)

    budget_header = http_request.headers.get("X-Timeout-Budget-Ms")
    timeout_budget_ms: float | None = None
    if budget_header:
        try:
            timeout_budget_ms = float(budget_header.strip())
        except ValueError:
            pass

    # Provider call first
    if timeout_budget_ms is not None:
        llm_response = await llmService.generate_completion(
            chat_req, api_key=api_key, timeout_budget_ms=timeout_budget_ms
        )
    else:
        llm_response = await llmService.generate_completion(chat_req, api_key=api_key)

    # Guard content=None: extract assistant content and persist atomic turn
    assistant_content = None
    if llm_response.choices and len(llm_response.choices) > 0:
        assistant_content = llm_response.choices[0].message.content

    if assistant_content is not None:
        svc.record_turn(
            conversation=conv,
            user_content=request.message,
            assistant_content=assistant_content,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    else:
        logger.warning(
            "LLM completion returned no content for conversation %s; turn not persisted.",
            conversation_id,
        )

    return BaseAPIResponse.success_response(llm_response)
