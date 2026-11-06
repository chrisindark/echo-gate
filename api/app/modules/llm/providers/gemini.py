"""Google Gemini (google-genai) adapter.

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

from fastapi import HTTPException
from google.genai import errors, types

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

logger = logging.getLogger(__name__)

_THOUGHT_SIGNATURES: dict[str, bytes] = {}
MAX_THOUGHT_SIGNATURES = 2048


def _store_thought_signature(call_id: str, sig: bytes | None) -> None:
    if not sig or not call_id:
        return
    if len(_THOUGHT_SIGNATURES) >= MAX_THOUGHT_SIGNATURES:
        keys = list(_THOUGHT_SIGNATURES.keys())[:512]
        for k in keys:
            _THOUGHT_SIGNATURES.pop(k, None)
    _THOUGHT_SIGNATURES[call_id] = sig


class GeminiProvider:
    def _prepare_gemini_payload(
        self, request: ChatCompletionRequest
    ) -> tuple[Any, types.GenerateContentConfig]:
        contents: Any = []
        system_instruction = None
        tool_call_names: dict[str, str] = {}

        for msg in request.messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "tool":
                fn_name = msg.name or tool_call_names.get(
                    msg.tool_call_id or "", "function"
                )
                resp_payload = {}
                if msg.content:
                    try:
                        parsed = json.loads(msg.content)
                        resp_payload = (
                            parsed if isinstance(parsed, dict) else {"result": parsed}
                        )
                    except Exception:
                        resp_payload = {"result": msg.content}
                part = types.Part.from_function_response(
                    name=fn_name, response=resp_payload
                )
                contents.append(types.Content(role="user", parts=[part]))
            else:
                role = "user" if msg.role == "user" else "model"
                parts = []
                if msg.content:
                    parts.append(types.Part.from_text(text=msg.content))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.id:
                            tool_call_names[tc.id] = tc.function.name
                        try:
                            args = (
                                json.loads(tc.function.arguments)
                                if tc.function.arguments
                                else {}
                            )
                            if not isinstance(args, dict):
                                args = {"value": args}
                        except Exception:
                            args = {}
                        part = types.Part.from_function_call(
                            name=tc.function.name,
                            args=args,
                        )
                        sig = _THOUGHT_SIGNATURES.get(tc.id or "")
                        if sig:
                            part.thought_signature = sig
                        parts.append(part)
                if not parts:
                    parts.append(types.Part.from_text(text=""))
                contents.append(types.Content(role=role, parts=parts))

        config_kwargs: dict[str, Any] = {}
        if request.temperature is not None:
            config_kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            config_kwargs["top_p"] = request.top_p
        if request.max_tokens is not None:
            config_kwargs["max_output_tokens"] = request.max_tokens
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if request.tools:
            function_declarations = []
            for tool in request.tools:
                fn = tool.function
                function_declarations.append(
                    types.FunctionDeclaration(
                        name=fn.name,
                        description=fn.description,
                        parameters_json_schema=fn.parameters,
                    )
                )
            if function_declarations:
                config_kwargs["tools"] = [
                    types.Tool(function_declarations=function_declarations)
                ]

        if request.tool_choice:
            tool_choice = request.tool_choice
            if isinstance(tool_choice, str):
                tc_lower = tool_choice.lower()
                if tc_lower == "auto":
                    config_kwargs["tool_config"] = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.AUTO
                        )
                    )
                elif tc_lower == "none":
                    config_kwargs["tool_config"] = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.NONE
                        )
                    )
                elif tc_lower == "required":
                    config_kwargs["tool_config"] = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.ANY
                        )
                    )
            elif isinstance(tool_choice, dict):
                fn_dict = tool_choice.get("function", {})
                fn_name = fn_dict.get("name") if isinstance(fn_dict, dict) else None
                if fn_name:
                    config_kwargs["tool_config"] = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.ANY,
                            allowed_function_names=[fn_name],
                        )
                    )

        if request.response_format:
            response_format_type = request.response_format.get("type")
            if response_format_type == "json_object":
                config_kwargs["response_mime_type"] = "application/json"
            elif response_format_type == "json_schema":
                config_kwargs["response_mime_type"] = "application/json"
                json_schema_obj = request.response_format.get("json_schema")
                schema = None
                if isinstance(json_schema_obj, dict):
                    schema = json_schema_obj.get("schema") or json_schema_obj
                elif "schema" in request.response_format:
                    schema = request.response_format.get("schema")
                if schema and isinstance(schema, dict):
                    config_kwargs["response_json_schema"] = schema

        if request.thinking or request.reasoning_effort:
            budget = (
                request.thinking_budget if request.thinking_budget is not None else -1
            )
            if hasattr(types, "ThinkingConfig"):
                try:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        thinking_budget=budget
                    )
                except Exception:
                    config_kwargs["thinking_config"] = {"thinking_budget": budget}
            else:
                config_kwargs["thinking_config"] = {"thinking_budget": budget}

        config = types.GenerateContentConfig(**config_kwargs)
        return contents, config

    @log_latency()
    async def generate_gemini_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.gemini_client:
            logger.error("Gemini API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Gemini API key is not configured"
            )

        logger.info(f"Forwarding request to Gemini ({request.model})...")
        contents, config = self._prepare_gemini_payload(request)

        try:
            response = await self.gemini_client.aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )

            prompt_tokens = (
                response.usage_metadata.prompt_token_count
                if response.usage_metadata
                else 0
            )
            completion_tokens = (
                response.usage_metadata.candidates_token_count
                if response.usage_metadata
                else 0
            )
            total_tokens = (
                response.usage_metadata.total_token_count
                if response.usage_metadata
                else 0
            )

            tool_calls = []
            candidate_fc_parts = []
            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                for p in response.candidates[0].content.parts:
                    if getattr(p, "function_call", None):
                        candidate_fc_parts.append(p)

            if response.function_calls:
                for idx, fc in enumerate(response.function_calls):
                    call_id = getattr(fc, "id", None) or f"call_{uuid.uuid4().hex}"
                    if idx < len(candidate_fc_parts):
                        sig = getattr(candidate_fc_parts[idx], "thought_signature", None)
                        if sig:
                            _store_thought_signature(call_id, sig)
                    try:
                        args_str = json.dumps(fc.args or {})
                    except Exception:
                        args_str = "{}"
                    tool_calls.append(
                        ToolCall(
                            id=call_id,
                            type="function",
                            function=FunctionCall(
                                name=fc.name,
                                arguments=args_str,
                            ),
                        )
                    )

            finish_reason = "tool_calls" if tool_calls else "stop"
            content_text = None
            reasoning_text = None

            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                thought_pieces = []
                text_pieces = []
                for p in response.candidates[0].content.parts:
                    if getattr(p, "thought", False):
                        if getattr(p, "text", None):
                            thought_pieces.append(p.text)
                    elif getattr(p, "text", None):
                        text_pieces.append(p.text)
                if thought_pieces:
                    reasoning_text = "".join(thought_pieces)
                if text_pieces:
                    content_text = "".join(text_pieces)
            elif response.text:
                content_text = response.text

            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                model=request.model,
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
        except errors.APIError as e:
            logger.error(f"Gemini API returned error: {e.code} - {e.message}")
            raise HTTPException(
                status_code=e.code, detail=f"Gemini API error: {e.message}"
            )
        except Exception as e:
            logger.error(f"Failed to call Gemini API: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

    async def generate_gemini_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if not self.gemini_client:
            logger.error("Gemini API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Gemini API key is not configured"
            )

        logger.info(f"Forwarding stream request to Gemini ({request.model})...")
        contents, config = self._prepare_gemini_payload(request)

        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_time = int(time.time())

        # 1. Yield initial role chunk
        yield ChatCompletionChunk(
            id=chunk_id,
            created=created_time,
            model=request.model,
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(role="assistant"),
                    finish_reason=None,
                )
            ],
        )

        had_tool_calls = False
        final_finish_reason: str | None = None
        usage: Usage | None = None
        tool_call_index = 0

        try:
            response_stream = (
                await self.gemini_client.aio.models.generate_content_stream(
                    model=request.model, contents=contents, config=config
                )
            )
            async for response in response_stream:
                thought_content = None
                text_content = None
                if (
                    response.candidates
                    and response.candidates[0].content
                    and response.candidates[0].content.parts
                ):
                    thought_pieces = []
                    text_pieces = []
                    for p in response.candidates[0].content.parts:
                        if getattr(p, "thought", False):
                            if getattr(p, "text", None):
                                thought_pieces.append(p.text)
                        elif getattr(p, "text", None):
                            text_pieces.append(p.text)
                    if thought_pieces:
                        thought_content = "".join(thought_pieces)
                    if text_pieces:
                        text_content = "".join(text_pieces)
                elif getattr(response, "text", None):
                    try:
                        text_content = response.text
                    except Exception:
                        pass

                if thought_content:
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        created=created_time,
                        model=request.model,
                        choices=[
                            ChunkChoice(
                                index=0,
                                delta=ChunkDelta(reasoning_content=thought_content),
                                finish_reason=None,
                            )
                        ],
                    )

                if text_content:
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        created=created_time,
                        model=request.model,
                        choices=[
                            ChunkChoice(
                                index=0,
                                delta=ChunkDelta(content=text_content),
                                finish_reason=None,
                            )
                        ],
                    )

                function_calls = []
                if (
                    response.candidates
                    and response.candidates[0].content
                    and response.candidates[0].content.parts
                ):
                    for part in response.candidates[0].content.parts:
                        if getattr(part, "function_call", None):
                            fc = part.function_call
                            function_calls.append(fc)
                            call_id = getattr(fc, "id", None) or f"call_{uuid.uuid4().hex}"
                            sig = getattr(part, "thought_signature", None)
                            if sig:
                                _store_thought_signature(call_id, sig)
                elif getattr(response, "function_calls", None):
                    function_calls = response.function_calls

                if function_calls:
                    had_tool_calls = True
                    tool_chunks = []
                    for fc in function_calls:
                        call_id = getattr(fc, "id", None) or f"call_{uuid.uuid4().hex}"
                        try:
                            args_str = json.dumps(getattr(fc, "args", {}) or {})
                        except Exception:
                            args_str = "{}"
                        tool_chunks.append(
                            ToolCallChunk(
                                index=tool_call_index,
                                id=call_id,
                                type="function",
                                function=FunctionCallChunk(
                                    name=fc.name,
                                    arguments=args_str,
                                ),
                            )
                        )
                        tool_call_index += 1

                    if tool_chunks:
                        yield ChatCompletionChunk(
                            id=chunk_id,
                            created=created_time,
                            model=request.model,
                            choices=[
                                ChunkChoice(
                                    index=0,
                                    delta=ChunkDelta(tool_calls=tool_chunks),
                                    finish_reason=None,
                                )
                            ],
                        )

                if response.candidates:
                    cand = response.candidates[0]
                    if cand.finish_reason:
                        fr_str = (
                            cand.finish_reason.value
                            if hasattr(cand.finish_reason, "value")
                            else str(cand.finish_reason)
                        ).upper()
                        if "STOP" in fr_str:
                            final_finish_reason = "stop"
                        elif "MAX_TOKENS" in fr_str:
                            final_finish_reason = "length"
                        elif "SAFETY" in fr_str:
                            final_finish_reason = "content_filter"

                if response.usage_metadata:
                    prompt_tokens = response.usage_metadata.prompt_token_count or 0
                    completion_tokens = (
                        response.usage_metadata.candidates_token_count or 0
                    )
                    total_tokens = response.usage_metadata.total_token_count or (
                        prompt_tokens + completion_tokens
                    )
                    usage = Usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )

            finish_reason = (
                "tool_calls" if had_tool_calls else (final_finish_reason or "stop")
            )
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(),
                        finish_reason=finish_reason,
                    )
                ],
                usage=usage,
            )
        except errors.APIError as e:
            logger.error(f"Gemini API returned error: {e.code} - {e.message}")
            raise HTTPException(
                status_code=e.code, detail=f"Gemini API error: {e.message}"
            )
        except Exception as e:
            logger.error(f"Failed to call Gemini API: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")
