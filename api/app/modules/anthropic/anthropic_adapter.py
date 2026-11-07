"""Bidirectional translation between Anthropic Messages API and internal chat models."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.modules.anthropic.anthropic_schema import (
    AnthropicMessagesRequest,
    AnthropicMessagesResponse,
    AnthropicUsage,
    format_anthropic_sse_event,
)
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    FunctionCall,
    FunctionDefinition,
    Tool,
    ToolCall,
)

logger = logging.getLogger(__name__)


def normalize_anthropic_to_chat_request(
    request: AnthropicMessagesRequest,
) -> ChatCompletionRequest:
    """Translate an incoming Anthropic Messages request into an internal ChatCompletionRequest."""
    chat_messages: list[ChatMessage] = []

    # 1. System prompt
    if request.system:
        if isinstance(request.system, str):
            chat_messages.append(ChatMessage(role="system", content=request.system))
        elif isinstance(request.system, list):
            sys_texts: list[str] = []
            for block in request.system:
                if isinstance(block, dict) and block.get("type") == "text":
                    sys_texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    sys_texts.append(block)
            if sys_texts:
                chat_messages.append(
                    ChatMessage(role="system", content="\n\n".join(sys_texts))
                )

    # 2. Conversational turns
    for msg in request.messages:
        role = msg.role
        if isinstance(msg.content, str):
            chat_messages.append(ChatMessage(role=role, content=msg.content))
        elif isinstance(msg.content, list):
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []

            for block in msg.content:
                if not isinstance(block, dict):
                    text_parts.append(str(block))
                    continue

                b_type = block.get("type")
                if b_type == "text":
                    text_parts.append(block.get("text", ""))
                elif b_type == "tool_use":
                    tc_id = block.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                    tc_name = block.get("name", "")
                    tc_input = block.get("input", {})
                    args_str = (
                        json.dumps(tc_input)
                        if isinstance(tc_input, dict)
                        else str(tc_input)
                    )
                    tool_calls.append(
                        ToolCall(
                            id=tc_id,
                            type="function",
                            function=FunctionCall(name=tc_name, arguments=args_str),
                        )
                    )
                elif b_type == "tool_result":
                    # Tool result corresponds to an OpenAI role="tool" turn
                    tc_id = block.get("tool_use_id", "")
                    result_content = block.get("content")
                    if isinstance(result_content, list):
                        result_str = "\n".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in result_content
                        )
                    else:
                        result_str = str(result_content or "")

                    chat_messages.append(
                        ChatMessage(
                            role="tool",
                            tool_call_id=tc_id,
                            content=result_str,
                        )
                    )

            if text_parts or tool_calls:
                combined_text = "\n\n".join(text_parts) if text_parts else None
                chat_messages.append(
                    ChatMessage(
                        role=role,
                        content=combined_text,
                        tool_calls=tool_calls if tool_calls else None,
                    )
                )

    # 3. Tools
    chat_tools: list[Tool] | None = None
    if request.tools:
        chat_tools = [
            Tool(
                type="function",
                function=FunctionDefinition(
                    name=t.name,
                    description=t.description or "",
                    parameters=t.input_schema,
                ),
            )
            for t in request.tools
        ]

    # Resolve service_name from model registry
    from app.core.config import config
    from app.modules.llm.model_registry import model_registry

    resolved_service = (
        request.service_name
        or model_registry.get_service_for_model(request.model)
        or (
            "claude"
            if request.model.lower().startswith("claude")
            else getattr(config, "DEFAULT_LLM_SERVICE", "claude")
        )
    )

    kwargs: dict[str, Any] = {
        "service_name": resolved_service,
        "model": request.model,
        "messages": chat_messages,
        "max_tokens": request.max_tokens,
        "stream": bool(request.stream),
        "tools": chat_tools,
        "prompt_optimization": request.prompt_optimization,
        "cache_enabled": request.cache_enabled,
        "cache_ttl": request.cache_ttl,
        "retries": request.retries,
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
    }
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.top_p is not None:
        kwargs["top_p"] = request.top_p
    if request.stop_sequences:
        kwargs["stop"] = request.stop_sequences

    return ChatCompletionRequest(**kwargs)


def normalize_chat_to_anthropic_response(
    response: ChatCompletionResponse,
) -> AnthropicMessagesResponse:
    """Translate an internal ChatCompletionResponse into an Anthropic Messages response."""
    content_blocks: list[dict[str, Any]] = []
    choice = response.choices[0] if response.choices else None
    stop_reason: str = "end_turn"

    if choice:
        msg = choice.message
        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})
        if msg.tool_calls:
            stop_reason = "tool_use"
            for tc in msg.tool_calls:
                parsed_args: Any = {}
                if tc.function and tc.function.arguments:
                    try:
                        parsed = json.loads(tc.function.arguments)
                        parsed_args = (
                            parsed if isinstance(parsed, dict) else {"raw": parsed}
                        )
                    except Exception:
                        parsed_args = {"raw": tc.function.arguments}
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.function.name if tc.function else "",
                        "input": parsed_args,
                    }
                )
        if choice.finish_reason == "length":
            stop_reason = "max_tokens"
        elif choice.finish_reason == "stop_sequence":
            stop_reason = "stop_sequence"

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    msg_id = response.id
    if msg_id.startswith("chatcmpl-"):
        msg_id = f"msg_{msg_id[9:]}"
    elif not msg_id.startswith("msg_"):
        msg_id = f"msg_{msg_id}"

    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    return AnthropicMessagesResponse(
        id=msg_id,
        type="message",
        role="assistant",
        model=response.model,
        content=content_blocks,
        stop_reason=stop_reason,
        usage=AnthropicUsage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        ),
    )


async def transform_chat_stream_to_anthropic_sse(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
    msg_id: str | None = None,
) -> AsyncIterator[str]:
    """Translate an internal ChatCompletionChunk stream into standard Anthropic SSE events."""
    msg_id = msg_id or f"msg_{uuid.uuid4().hex[:24]}"

    yield format_anthropic_sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 1},
            },
        },
    )

    started_text_block = False
    block_index = 0
    final_stop_reason = "end_turn"
    total_output_tokens = 0

    async for chunk in stream:
        if not chunk.choices:
            if chunk.usage:
                total_output_tokens = chunk.usage.completion_tokens
            continue

        choice = chunk.choices[0]
        delta = choice.delta
        finish_reason = choice.finish_reason

        if finish_reason:
            if finish_reason == "length":
                final_stop_reason = "max_tokens"
            elif finish_reason == "tool_calls":
                final_stop_reason = "tool_use"
            else:
                final_stop_reason = "end_turn"

        if delta.content:
            if not started_text_block:
                started_text_block = True
                yield format_anthropic_sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            total_output_tokens += 1
            yield format_anthropic_sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": delta.content},
                },
            )

        if delta.tool_calls:
            for tc in delta.tool_calls:
                if tc.function and tc.function.name:
                    if started_text_block:
                        yield format_anthropic_sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": block_index},
                        )
                        block_index += 1
                        started_text_block = False
                    yield format_anthropic_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tc.id or f"toolu_{uuid.uuid4().hex[:16]}",
                                "name": tc.function.name,
                                "input": {},
                            },
                        },
                    )
                if tc.function and tc.function.arguments:
                    yield format_anthropic_sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": tc.function.arguments,
                            },
                        },
                    )

    if started_text_block:
        yield format_anthropic_sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )

    yield format_anthropic_sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": final_stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": max(1, total_output_tokens)},
        },
    )

    yield format_anthropic_sse_event(
        "message_stop",
        {"type": "message_stop"},
    )
