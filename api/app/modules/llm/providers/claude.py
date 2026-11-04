"""Anthropic Claude adapter.

Extracted verbatim from the former single-file `LlmProviderService`. The methods
still operate on the dispatcher instance (``self``), which owns every provider
credential and client; only the owning class changed.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import config
from app.core.logger import log_latency
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    FunctionCall,
    FunctionCallChunk,
    ToolCall,
    ToolCallChunk,
    Usage,
)
from app.modules.llm.providers.common import (
    extract_http_error_detail as _extract_http_error_detail,
)

logger = logging.getLogger(__name__)


class ClaudeProvider:
    def _prepare_claude_payload(
        self, request: ChatCompletionRequest, stream: bool = False
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = self.claude_base_url.rstrip("/")
        if not url.endswith("/messages"):
            if url.endswith("/v1"):
                url = f"{url}/messages"
            else:
                url = f"{url}/v1/messages"

        headers: dict[str, str] = {
            "x-api-key": self.claude_api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        system_prompts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in request.messages:
            if msg.role == "system":
                if msg.content:
                    system_prompts.append(msg.content)
            elif msg.role == "tool":
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                }
                if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                    content = anthropic_messages[-1]["content"]
                    if isinstance(content, list):
                        content.append(tool_result_block)
                    else:
                        anthropic_messages[-1]["content"] = [
                            {"type": "text", "text": str(content)},
                            tool_result_block,
                        ]
                else:
                    anthropic_messages.append(
                        {"role": "user", "content": [tool_result_block]}
                    )
            elif msg.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parsed_input: Any = {}
                        if tc.function.arguments:
                            try:
                                parsed = json.loads(tc.function.arguments)
                                parsed_input = (
                                    parsed
                                    if isinstance(parsed, dict)
                                    else {"value": parsed}
                                )
                            except Exception:
                                parsed_input = {}
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.function.name,
                                "input": parsed_input,
                            }
                        )
                if not blocks:
                    blocks.append({"type": "text", "text": ""})

                if anthropic_messages and anthropic_messages[-1]["role"] == "assistant":
                    content = anthropic_messages[-1]["content"]
                    if isinstance(content, list):
                        content.extend(blocks)
                    else:
                        anthropic_messages[-1]["content"] = [
                            {"type": "text", "text": str(content)}
                        ] + blocks
                else:
                    anthropic_messages.append({"role": "assistant", "content": blocks})
            else:
                user_text_block = {"type": "text", "text": msg.content or ""}
                if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                    content = anthropic_messages[-1]["content"]
                    if isinstance(content, list):
                        content.append(user_text_block)
                    else:
                        anthropic_messages[-1]["content"] = [
                            {"type": "text", "text": str(content)},
                            user_text_block,
                        ]
                else:
                    anthropic_messages.append(
                        {"role": "user", "content": [user_text_block]}
                    )

        if not anthropic_messages:
            anthropic_messages.append(
                {"role": "user", "content": [{"type": "text", "text": ""}]}
            )
        elif anthropic_messages[0]["role"] != "user":
            anthropic_messages.insert(
                0, {"role": "user", "content": [{"type": "text", "text": ""}]}
            )

        payload_data: dict[str, Any] = {
            "model": request.model,
            "messages": anthropic_messages,
            "max_tokens": (
                request.max_tokens if request.max_tokens is not None else 4096
            ),
        }

        if request.thinking or request.reasoning_effort:
            budget = (
                request.thinking_budget if request.thinking_budget is not None else 2048
            )
            payload_data["thinking"] = {"type": "enabled", "budget_tokens": budget}
            if payload_data.get("max_tokens", 4096) <= budget:
                payload_data["max_tokens"] = budget + 2048
            payload_data["temperature"] = 1.0

        if system_prompts:
            payload_data["system"] = "\n\n".join(system_prompts)

        if request.temperature is not None:
            payload_data["temperature"] = request.temperature

        if request.top_p is not None:
            payload_data["top_p"] = request.top_p

        if request.stop:
            payload_data["stop_sequences"] = request.stop

        if request.tools and request.tool_choice != "none":
            anthropic_tools = []
            for t in request.tools:
                fn = t.function
                tool_dict: dict[str, Any] = {
                    "name": fn.name,
                    "description": fn.description or "",
                    "input_schema": (
                        fn.parameters
                        if fn.parameters
                        else {"type": "object", "properties": {}}
                    ),
                }
                anthropic_tools.append(tool_dict)
            if anthropic_tools:
                payload_data["tools"] = anthropic_tools

        if request.tool_choice and "tools" in payload_data:
            tc = request.tool_choice
            if isinstance(tc, str):
                tc_lower = tc.lower()
                if tc_lower == "auto":
                    payload_data["tool_choice"] = {"type": "auto"}
                elif tc_lower in ["required", "any"]:
                    payload_data["tool_choice"] = {"type": "any"}
            elif isinstance(tc, dict):
                fn_dict = tc.get("function", {})
                fn_name = fn_dict.get("name") if isinstance(fn_dict, dict) else None
                if fn_name:
                    payload_data["tool_choice"] = {"type": "tool", "name": fn_name}
                elif tc.get("type") in ["auto", "any", "tool"]:
                    payload_data["tool_choice"] = tc

        if stream:
            payload_data["stream"] = True

        return url, headers, payload_data

    @log_latency()
    async def generate_claude_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.claude_api_key:
            logger.error("Claude API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Claude API key is not configured"
            )

        url, headers, payload_data = self._prepare_claude_payload(request, stream=False)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(
                    f"Forwarding request to upstream Claude ({request.model})..."
                )
                response = await client.post(
                    url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()

                text_pieces: list[str] = []
                thinking_pieces: list[str] = []
                tool_calls: list[ToolCall] = []

                for block in data.get("content", []):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_pieces.append(block.get("text", ""))
                    elif block_type == "thinking":
                        thinking_pieces.append(block.get("thinking", ""))
                    elif block_type == "tool_use":
                        call_id = block.get("id") or f"call_{uuid.uuid4().hex}"
                        fn_name = block.get("name", "")
                        input_args = block.get("input", {})
                        try:
                            args_str = (
                                json.dumps(input_args)
                                if isinstance(input_args, (dict, list))
                                else str(input_args)
                            )
                        except Exception:
                            args_str = "{}"
                        tool_calls.append(
                            ToolCall(
                                id=call_id,
                                type="function",
                                function=FunctionCall(
                                    name=fn_name,
                                    arguments=args_str,
                                ),
                            )
                        )

                stop_reason = data.get("stop_reason")
                if tool_calls:
                    finish_reason = "tool_calls"
                elif stop_reason == "end_turn":
                    finish_reason = "stop"
                elif stop_reason == "max_tokens":
                    finish_reason = "length"
                elif stop_reason == "stop_sequence":
                    finish_reason = "stop"
                elif stop_reason == "tool_use":
                    finish_reason = "tool_calls"
                else:
                    finish_reason = stop_reason or "stop"

                content_text = "".join(text_pieces) if text_pieces else None
                reasoning_text = "".join(thinking_pieces) if thinking_pieces else None

                usage_data = data.get("usage", {})
                prompt_tokens = usage_data.get("input_tokens", 0)
                completion_tokens = usage_data.get("output_tokens", 0)
                total_tokens = prompt_tokens + completion_tokens

                return ChatCompletionResponse(
                    id=data.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
                    created=int(time.time()),
                    model=data.get("model", request.model),
                    choices=[
                        Choice(
                            index=0,
                            message=ChatMessage(
                                role="assistant",
                                content=content_text,
                                reasoning_content=reasoning_text,
                                tool_calls=tool_calls if tool_calls else None,
                            ),
                            finish_reason=finish_reason,
                        )
                    ],
                    usage=Usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    ),
                )
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "Claude LLM error")
            logger.error(
                f"Claude API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call Claude LLM: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

    async def generate_claude_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if not self.claude_api_key:
            logger.error("Claude API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Claude API key is not configured"
            )

        url, headers, payload_data = self._prepare_claude_payload(request, stream=True)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(
                    f"Forwarding stream request to upstream Claude ({request.model})..."
                )
                async with client.stream(
                    "POST",
                    url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        detail = _extract_http_error_detail(
                            response, "Claude LLM error"
                        )
                        logger.error(
                            f"Claude API returned HTTP error: {response.status_code} - {response.text}"
                        )
                        raise HTTPException(
                            status_code=response.status_code, detail=detail
                        )

                    msg_id = f"chatcmpl-{uuid.uuid4().hex}"
                    created_time = int(time.time())
                    model_name = request.model
                    prompt_tokens = 0
                    completion_tokens = 0
                    finish_reason_sent = False
                    block_index_to_tool_index: dict[int, int] = {}
                    next_tool_index = 0

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if not data_str:
                                continue
                            try:
                                event_data = json.loads(data_str)
                            except Exception:
                                continue

                            event_type = event_data.get("type")

                            if event_type == "message_start":
                                msg_obj = event_data.get("message", {})
                                msg_id = msg_obj.get("id", msg_id)
                                model_name = msg_obj.get("model", model_name)
                                usage_obj = msg_obj.get("usage", {})
                                prompt_tokens = usage_obj.get("input_tokens", 0)

                                yield ChatCompletionChunk(
                                    id=msg_id,
                                    created=created_time,
                                    model=model_name,
                                    choices=[
                                        ChunkChoice(
                                            index=0,
                                            delta=ChunkDelta(role="assistant"),
                                            finish_reason=None,
                                        )
                                    ],
                                )

                            elif event_type == "content_block_start":
                                block_idx = event_data.get("index", 0)
                                content_block = event_data.get("content_block", {})
                                if content_block.get("type") == "tool_use":
                                    tool_idx = next_tool_index
                                    next_tool_index += 1
                                    block_index_to_tool_index[block_idx] = tool_idx
                                    yield ChatCompletionChunk(
                                        id=msg_id,
                                        created=created_time,
                                        model=model_name,
                                        choices=[
                                            ChunkChoice(
                                                index=0,
                                                delta=ChunkDelta(
                                                    tool_calls=[
                                                        ToolCallChunk(
                                                            index=tool_idx,
                                                            id=content_block.get("id"),
                                                            type="function",
                                                            function=FunctionCallChunk(
                                                                name=content_block.get(
                                                                    "name"
                                                                ),
                                                                arguments="",
                                                            ),
                                                        )
                                                    ]
                                                ),
                                                finish_reason=None,
                                            )
                                        ],
                                    )

                            elif event_type == "content_block_delta":
                                block_idx = event_data.get("index", 0)
                                delta_obj = event_data.get("delta", {})
                                delta_type = delta_obj.get("type")

                                if delta_type == "text_delta":
                                    text_piece = delta_obj.get("text", "")
                                    if text_piece:
                                        yield ChatCompletionChunk(
                                            id=msg_id,
                                            created=created_time,
                                            model=model_name,
                                            choices=[
                                                ChunkChoice(
                                                    index=0,
                                                    delta=ChunkDelta(
                                                        content=text_piece
                                                    ),
                                                    finish_reason=None,
                                                )
                                            ],
                                        )
                                elif delta_type == "thinking_delta":
                                    thinking_piece = delta_obj.get("thinking", "")
                                    if thinking_piece:
                                        yield ChatCompletionChunk(
                                            id=msg_id,
                                            created=created_time,
                                            model=model_name,
                                            choices=[
                                                ChunkChoice(
                                                    index=0,
                                                    delta=ChunkDelta(
                                                        reasoning_content=thinking_piece
                                                    ),
                                                    finish_reason=None,
                                                )
                                            ],
                                        )
                                elif delta_type == "input_json_delta":
                                    partial_json = delta_obj.get("partial_json", "")
                                    tool_idx = block_index_to_tool_index.get(
                                        block_idx, 0
                                    )
                                    yield ChatCompletionChunk(
                                        id=msg_id,
                                        created=created_time,
                                        model=model_name,
                                        choices=[
                                            ChunkChoice(
                                                index=0,
                                                delta=ChunkDelta(
                                                    tool_calls=[
                                                        ToolCallChunk(
                                                            index=tool_idx,
                                                            function=FunctionCallChunk(
                                                                arguments=partial_json
                                                            ),
                                                        )
                                                    ]
                                                ),
                                                finish_reason=None,
                                            )
                                        ],
                                    )

                            elif event_type == "message_delta":
                                delta_obj = event_data.get("delta", {})
                                stop_reason = delta_obj.get("stop_reason")
                                usage_obj = event_data.get("usage", {})
                                completion_tokens = usage_obj.get("output_tokens", 0)

                                if stop_reason == "tool_use":
                                    finish_reason = "tool_calls"
                                elif stop_reason == "end_turn":
                                    finish_reason = "stop"
                                elif stop_reason == "max_tokens":
                                    finish_reason = "length"
                                elif stop_reason == "stop_sequence":
                                    finish_reason = "stop"
                                else:
                                    finish_reason = stop_reason or "stop"

                                total_tokens = prompt_tokens + completion_tokens
                                finish_reason_sent = True
                                yield ChatCompletionChunk(
                                    id=msg_id,
                                    created=created_time,
                                    model=model_name,
                                    choices=[
                                        ChunkChoice(
                                            index=0,
                                            delta=ChunkDelta(),
                                            finish_reason=finish_reason,
                                        )
                                    ],
                                    usage=Usage(
                                        prompt_tokens=prompt_tokens,
                                        completion_tokens=completion_tokens,
                                        total_tokens=total_tokens,
                                    ),
                                )

                            elif event_type == "message_stop":
                                if not finish_reason_sent:
                                    total_tokens = prompt_tokens + completion_tokens
                                    finish_reason_sent = True
                                    yield ChatCompletionChunk(
                                        id=msg_id,
                                        created=created_time,
                                        model=model_name,
                                        choices=[
                                            ChunkChoice(
                                                index=0,
                                                delta=ChunkDelta(),
                                                finish_reason="stop",
                                            )
                                        ],
                                        usage=Usage(
                                            prompt_tokens=prompt_tokens,
                                            completion_tokens=completion_tokens,
                                            total_tokens=total_tokens,
                                        ),
                                    )

                            elif event_type == "error":
                                err_msg = event_data.get("error", {}).get(
                                    "message", "Claude stream error"
                                )
                                logger.error(f"Claude stream error: {err_msg}")
                                raise HTTPException(
                                    status_code=502,
                                    detail=f"Claude error: {err_msg}",
                                )
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "Claude LLM error")
            logger.error(
                f"Claude API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call Claude LLM: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")
