import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.constants import EchoGateHeaders
from app.core.database import get_db
from app.core.dependencies import get_llm_router_service
from app.core.error_classification import classify_provider_error
from app.core.guards import (
    api_key_auth_optional_guard,
    llm_quota_guard,
    resolve_caller_user,
)
from app.core.responses import BaseAPIResponse
from app.core.rls import ANONYMOUS_TENANT
from app.modules.chat.chat_schema import (
    SSE_DONE_EVENT,
    ChatCompletionRequest,
    ChatMessage,
    format_sse_event,
)
from app.modules.conversation.conversation_schema import (
    ChatCompletionRequestWithConversation,
    ConversationCreate,
    ConversationDeleteResponse,
    ConversationResponse,
    ConversationUpdate,
    ConversationWithMessagesResponse,
)
from app.modules.conversation.conversation_service import ConversationService
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations"])


def _persist_conversation_turn(
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    user_content: str,
    assistant_content: str,
    fallback_db: Session | None = None,
) -> None:
    """Atomically record the user turn and assistant reply after a stream completes."""
    # Try using task_db_session first (production background session)
    try:
        from app.core.database import task_db_session

        with task_db_session() as tdb:
            svc = ConversationService(tdb)
            conv = svc.get_conversation(
                conversation_id, user_id=user_id, tenant_id=tenant_id
            )
            if conv:
                svc.record_turn(
                    conversation=conv,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                return
    except Exception:
        pass

    # Fallback to current request's db session (e.g. SQLite test harness)
    if fallback_db is not None:
        try:
            svc = ConversationService(fallback_db)
            conv = svc.get_conversation(
                conversation_id, user_id=user_id, tenant_id=tenant_id
            )
            if conv:
                svc.record_turn(
                    conversation=conv,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
        except Exception as err:
            logger.error("Failed to persist streaming conversation turn: %s", err)


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


@api_v1_router.patch(
    "/{conversation_id}",
    response_model=BaseAPIResponse[ConversationResponse],
    dependencies=[Depends(api_key_auth_optional_guard)],
)
def update_conversation(
    http_request: Request,
    conversation_id: str,
    request: ConversationUpdate,
    db: Session = Depends(get_db),
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)
    conv = svc.update_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        title=request.title,
        tenant_id=tenant_id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return BaseAPIResponse.success_response(conv)


@api_v1_router.delete(
    "/{conversation_id}",
    response_model=BaseAPIResponse[ConversationDeleteResponse],
    dependencies=[Depends(api_key_auth_optional_guard)],
)
def delete_conversation(
    http_request: Request, conversation_id: str, db: Session = Depends(get_db)
):
    user_id = resolve_caller_user(http_request)
    tenant_id = getattr(http_request.state, "tenant_id", None) or ANONYMOUS_TENANT
    svc = ConversationService(db)
    deleted = svc.delete_conversation(
        conversation_id=conversation_id, user_id=user_id, tenant_id=tenant_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return BaseAPIResponse.success_response(
        ConversationDeleteResponse(id=conversation_id, deleted=True)
    )


@api_v1_router.post(
    "/{conversation_id}/chat",
    dependencies=[Depends(api_key_auth_optional_guard), Depends(llm_quota_guard)],
)
async def chat_in_conversation(
    http_request: Request,
    http_response: Response,
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
        max_tokens=request.max_tokens,
        stream=request.stream,
        thinking=request.thinking,
        thinking_budget=request.thinking_budget,
        response_format=request.response_format,
        prompt_optimization=request.prompt_optimization,
        user=user_id,
        conversation_id=conversation_id,
    )
    chat_req.tenant_id = tenant_id
    chat_req.user_id = getattr(http_request.state, "user_id", None)
    chat_req.session_id = http_request.headers.get(EchoGateHeaders.SESSION_ID)

    # Header-level prompt optimization parsing fallback
    opt_header = http_request.headers.get(
        EchoGateHeaders.PROMPT_OPTIMIZATION
    ) or http_request.headers.get("X-Prompt-Optimization")

    if chat_req.prompt_optimization is None and opt_header:
        val = opt_header.strip().lower()
        ccr_turns_header = http_request.headers.get(
            EchoGateHeaders.PROMPT_OPTIMIZATION_CCR_TURNS
        )
        ccr_turns = (
            int(ccr_turns_header.strip())
            if ccr_turns_header and ccr_turns_header.strip().isdigit()
            else 5
        )
        if val in ("true", "1", "yes", "on"):
            chat_req.prompt_optimization = PromptOptimizationConfig(
                enabled=True,
                ccr_keep_last_n_turns=ccr_turns,
            )
        elif val in ("false", "0", "no", "off"):
            chat_req.prompt_optimization = PromptOptimizationConfig(enabled=False)
        else:
            strategies = [s.strip().lower() for s in val.split(",")]
            chat_req.prompt_optimization = PromptOptimizationConfig(
                enabled=True,
                whitespace="whitespace" in strategies,
                session_dedup="session-dedup" in strategies,
                json_table="json-table" in strategies,
                ccr="ccr" in strategies,
                ccr_keep_last_n_turns=ccr_turns,
                headroom_trim="headroom-trim" in strategies or "headroom" in strategies,
            )

    api_key = getattr(http_request.state, "api_key", None)

    budget_header = http_request.headers.get("X-Timeout-Budget-Ms")
    timeout_budget_ms: float | None = None
    if budget_header:
        try:
            timeout_budget_ms = float(budget_header.strip())
        except ValueError:
            pass

    if request.stream:
        if timeout_budget_ms is not None:
            stream_iter = llmService.generate_stream(
                chat_req, api_key=api_key, timeout_budget_ms=timeout_budget_ms
            ).__aiter__()
        else:
            stream_iter = llmService.generate_stream(
                chat_req, api_key=api_key
            ).__aiter__()

        first_chunk = None
        try:
            first_chunk = await stream_iter.__anext__()
        except StopAsyncIteration:
            pass

        async def sse_event_generator():
            accumulated_content: list[str] = []
            try:
                if first_chunk is not None:
                    if first_chunk.choices:
                        delta = getattr(first_chunk.choices[0], "delta", None)
                        if delta and delta.content:
                            accumulated_content.append(delta.content)
                    yield format_sse_event(first_chunk)

                async for chunk in stream_iter:
                    if chunk.choices:
                        delta = getattr(chunk.choices[0], "delta", None)
                        if delta and delta.content:
                            accumulated_content.append(delta.content)
                    yield format_sse_event(chunk)

                yield SSE_DONE_EVENT

                full_reply = "".join(accumulated_content)
                if full_reply:
                    _persist_conversation_turn(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        user_content=request.message,
                        assistant_content=full_reply,
                        fallback_db=db,
                    )
                else:
                    logger.warning(
                        "Streaming completion for conversation %s had no content; turn not persisted.",
                        conversation_id,
                    )
            except Exception as e:
                logger.error("Error during streaming conversation completion: %s", e)
                provider = chat_req.service_name or "upstream"
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
            EchoGateHeaders.GATEWAY_DEGRADATION: getattr(
                first_chunk, "_degradation_state", None
            )
            or "none",
        }
        if hasattr(quota_status, "to_headers"):
            stream_headers.update(quota_status.to_headers())

        first_opt = getattr(first_chunk, "_prompt_optimization_result", None)
        if first_opt and first_opt.applied:
            stream_headers[EchoGateHeaders.PROMPT_OPTIMIZATION] = "applied"
            stream_headers[EchoGateHeaders.PROMPT_SAVINGS] = (
                f"tokens={first_opt.tokens_saved},chars={first_opt.chars_saved}"
            )

        return StreamingResponse(
            sse_event_generator(),
            media_type="text/event-stream",
            headers=stream_headers,
        )

    # Non-streaming provider call
    if timeout_budget_ms is not None:
        llm_response = await llmService.generate_completion(
            chat_req, api_key=api_key, timeout_budget_ms=timeout_budget_ms
        )
    else:
        llm_response = await llmService.generate_completion(chat_req, api_key=api_key)

    degradation = getattr(llm_response, "_degradation_state", None) or "none"
    http_response.headers[EchoGateHeaders.GATEWAY_DEGRADATION] = degradation
    opt_res = getattr(llm_response, "_prompt_optimization_result", None)
    if opt_res and opt_res.applied:
        http_response.headers[EchoGateHeaders.PROMPT_OPTIMIZATION] = "applied"
        http_response.headers[EchoGateHeaders.PROMPT_SAVINGS] = (
            f"tokens={opt_res.tokens_saved},chars={opt_res.chars_saved}"
        )
    cache_info = getattr(llm_response, "cache_info", None) or {}
    if cache_info.get("cache_hit"):
        http_response.headers[EchoGateHeaders.CACHE] = "HIT"
    else:
        http_response.headers[EchoGateHeaders.CACHE] = "MISS"

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
