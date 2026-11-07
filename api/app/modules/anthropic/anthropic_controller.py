"""FastAPI controller for Anthropic Messages API (POST /api/v1/messages)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.modules.anthropic.anthropic_adapter import (
    normalize_anthropic_to_chat_request,
    normalize_chat_to_anthropic_response,
    transform_chat_stream_to_anthropic_sse,
)
from app.modules.anthropic.anthropic_schema import (
    AnthropicMessagesRequest,
    AnthropicMessagesResponse,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)

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

    chat_req = normalize_anthropic_to_chat_request(request)
    api_key = x_api_key or getattr(http_request.state, "api_key", None)

    # Required response header for Anthropic clients
    http_response.headers["anthropic-version"] = "2023-06-01"

    if request.stream:
        stream_gen = llm_router_service.generate_stream(chat_req, api_key=api_key)
        if hasattr(stream_gen, "__await__"):
            stream_gen = await stream_gen
        sse_stream = transform_chat_stream_to_anthropic_sse(
            stream_gen, model=request.model
        )
        return StreamingResponse(
            sse_stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "anthropic-version": "2023-06-01",
            },
        )

    response = await llm_router_service.generate_completion(chat_req, api_key=api_key)

    # Propagate cache and optimization status headers
    cache_info = getattr(response, "cache_info", None) or {}
    cache_status = (
        "HIT"
        if (isinstance(cache_info, dict) and cache_info.get("matched"))
        else "MISS"
    )
    http_response.headers[EchoGateHeaders.CACHE] = cache_status

    degradation = getattr(response, "_degradation_state", None) or "none"
    http_response.headers[EchoGateHeaders.GATEWAY_DEGRADATION] = degradation

    opt_res = getattr(response, "_prompt_optimization_result", None)
    if opt_res and getattr(opt_res, "applied", False):
        http_response.headers[EchoGateHeaders.PROMPT_OPTIMIZATION] = "applied"
        http_response.headers[EchoGateHeaders.PROMPT_SAVINGS] = (
            f"tokens={opt_res.tokens_saved},chars={opt_res.chars_saved}"
        )

    return normalize_chat_to_anthropic_response(response)


@anthropic_router.post(
    "/api/v1/messages",
    response_model=AnthropicMessagesResponse,
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
