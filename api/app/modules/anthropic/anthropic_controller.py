"""FastAPI controller for Anthropic Messages API (POST /api/v1/messages)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.error_envelopes import ANTHROPIC_ERROR_RESPONSES
from app.core.guards import (
    api_key_auth_optional_guard,
    enforce_anonymous_provider_access,
    llm_quota_guard,
)
from app.modules.anthropic.anthropic_adapter import (
    normalize_anthropic_to_chat_request,
    normalize_chat_to_anthropic_response,
    transform_chat_stream_to_anthropic_sse,
)
from app.modules.anthropic.anthropic_schema import (
    AnthropicMessagesRequest,
    AnthropicMessagesResponse,
)
from app.modules.chat.chat_schema import apply_request_directives
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)
from app.modules.tenant_policy.tenant_policy_service import TenantPolicyService

logger = logging.getLogger(__name__)

anthropic_router = APIRouter(tags=["Anthropic Messages"])


async def _handle_messages_request(
    request: AnthropicMessagesRequest,
    http_request: Request,
    http_response: Response,
    llm_router_service: LlmRouterService,
    x_api_key: str | None = None,
):
    # Header-level prompt optimization opt-in
    opt_header = http_request.headers.get(
        EchoGateHeaders.PROMPT_OPTIMIZATION
    ) or http_request.headers.get("X-Prompt-Optimization")

    if request.prompt_optimization is None and opt_header:
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
            request.prompt_optimization = PromptOptimizationConfig(
                enabled=True,
                ccr_keep_last_n_turns=ccr_turns,
            )
        elif val in ("false", "0", "no", "off"):
            request.prompt_optimization = PromptOptimizationConfig(enabled=False)
        else:
            strategies = [s.strip().lower() for s in val.split(",")]
            request.prompt_optimization = PromptOptimizationConfig(
                enabled=True,
                whitespace="whitespace" in strategies,
                session_dedup="session-dedup" in strategies,
                json_table="json-table" in strategies,
                ccr="ccr" in strategies,
                ccr_keep_last_n_turns=ccr_turns,
                headroom_trim=(
                    "headroom-trim" in strategies or "headroom" in strategies
                ),
                compress="compress" in strategies,
            )

    apply_request_directives(request, http_request.headers)
    chat_req = normalize_anthropic_to_chat_request(request)
    chat_req.tenant_id = getattr(http_request.state, "tenant_id", None)
    chat_req.user_id = getattr(http_request.state, "user_id", None)
    policy_svc = getattr(llm_router_service, "tenant_policy_service", None)
    if isinstance(policy_svc, TenantPolicyService):
        policy_svc.resolve_and_apply(chat_req)
    enforce_anonymous_provider_access(http_request, chat_req)
    api_key = x_api_key or getattr(http_request.state, "api_key", None)

    # Required response header for Anthropic clients
    http_response.headers["anthropic-version"] = "2023-06-01"

    if request.stream:
        stream_gen = llm_router_service.generate_stream(chat_req, api_key=api_key)
        if hasattr(stream_gen, "__await__"):
            stream_gen = await stream_gen

        stream_iter = stream_gen.__aiter__()
        first_chunk = None
        try:
            first_chunk = await stream_iter.__anext__()
        except StopAsyncIteration:
            pass

        async def _chained_chunks():
            if first_chunk is not None:
                yield first_chunk
            async for chunk in stream_iter:
                yield chunk

        sse_stream = transform_chat_stream_to_anthropic_sse(
            _chained_chunks(), model=request.model
        )
        stream_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "anthropic-version": "2023-06-01",
            EchoGateHeaders.GATEWAY_DEGRADATION: getattr(
                first_chunk, "_degradation_state", None
            )
            or "none",
        }
        if chat_req.cache_bypassed:
            stream_headers[EchoGateHeaders.CACHE] = "BYPASS"
        if chat_req.disable_log:
            stream_headers[EchoGateHeaders.DISABLE_LOG] = "true"
        first_pii = getattr(first_chunk, "_pii_scrubbing_result", None)
        if first_pii and getattr(first_pii, "applied", False):
            stream_headers[EchoGateHeaders.PII_REDACTED] = first_pii.to_header_value()
        first_guard = getattr(first_chunk, "_guardrail_result", None)
        if first_guard and getattr(first_guard, "applied", False):
            guard_header = first_guard.to_header_value()
            if guard_header:
                stream_headers[EchoGateHeaders.GUARDRAIL_STATUS] = guard_header
        return StreamingResponse(
            sse_stream,
            media_type="text/event-stream",
            headers=stream_headers,
        )

    response = await llm_router_service.generate_completion(chat_req, api_key=api_key)

    # Propagate cache and optimization status headers
    cache_info = getattr(response, "cache_info", None) or {}
    if chat_req.cache_bypassed:
        cache_status = "BYPASS"
    elif isinstance(cache_info, dict) and cache_info.get("matched"):
        cache_status = "HIT"
    else:
        cache_status = "MISS"
    http_response.headers[EchoGateHeaders.CACHE] = cache_status
    if chat_req.disable_log:
        http_response.headers[EchoGateHeaders.DISABLE_LOG] = "true"

    degradation = getattr(response, "_degradation_state", None) or "none"
    http_response.headers[EchoGateHeaders.GATEWAY_DEGRADATION] = degradation

    opt_res = getattr(response, "_prompt_optimization_result", None)
    if opt_res and getattr(opt_res, "applied", False):
        http_response.headers[EchoGateHeaders.PROMPT_OPTIMIZATION] = "applied"
        http_response.headers[EchoGateHeaders.PROMPT_SAVINGS] = (
            f"tokens={opt_res.tokens_saved},chars={opt_res.chars_saved}"
        )
    pii_res = getattr(response, "_pii_scrubbing_result", None)
    if pii_res and getattr(pii_res, "applied", False):
        http_response.headers[EchoGateHeaders.PII_REDACTED] = pii_res.to_header_value()
    guard_res = getattr(response, "_guardrail_result", None)
    if guard_res and getattr(guard_res, "applied", False):
        guard_header = guard_res.to_header_value()
        if guard_header:
            http_response.headers[EchoGateHeaders.GUARDRAIL_STATUS] = guard_header

    return normalize_chat_to_anthropic_response(response)


@anthropic_router.post(
    "/api/v1/messages",
    response_model=AnthropicMessagesResponse,
    responses=ANTHROPIC_ERROR_RESPONSES,
    dependencies=[Depends(api_key_auth_optional_guard), Depends(llm_quota_guard)],
)
async def create_message(
    request: AnthropicMessagesRequest,
    http_request: Request,
    http_response: Response,
    x_api_key: str | None = Header(default=None),
    llm_router_service: LlmRouterService = Depends(get_llm_router_service),
):
    """Anthropic Messages API endpoint."""
    return await _handle_messages_request(
        request,
        http_request,
        http_response,
        llm_router_service,
        x_api_key=x_api_key,
    )


@anthropic_router.post(
    "/v1/messages",
    response_model=AnthropicMessagesResponse,
    dependencies=[Depends(api_key_auth_optional_guard), Depends(llm_quota_guard)],
    include_in_schema=False,
)
async def create_message_v1_alias(
    request: AnthropicMessagesRequest,
    http_request: Request,
    http_response: Response,
    x_api_key: str | None = Header(default=None),
    llm_router_service: LlmRouterService = Depends(get_llm_router_service),
):
    """Compatibility alias for SDKs using root base URLs."""
    return await _handle_messages_request(
        request,
        http_request,
        http_response,
        llm_router_service,
        x_api_key=x_api_key,
    )
