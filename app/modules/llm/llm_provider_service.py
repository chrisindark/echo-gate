import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import errors, types

from app.core.config import config
from app.core.constants import MOCK_RESPONSE_TEXT
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


def _extract_http_error_detail(response: httpx.Response, fallback_msg: str) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and "message" in err:
                return f"{fallback_msg}: {err['message']}"
            elif isinstance(err, str):
                return f"{fallback_msg}: {err}"
            elif "message" in data:
                return f"{fallback_msg}: {data['message']}"
    except Exception:
        pass
    text = response.text
    if text:
        return f"{fallback_msg}: {text}"
    return fallback_msg


def _prepare_openai_compatible_payload(
    request: ChatCompletionRequest, stream: bool = False
) -> dict[str, Any]:
    payload_data = request.model_dump(exclude_unset=True)
    for key in [
        "service_name",
        "thinking",
        "thinking_budget",
        "user_id",
        "tenant_id",
        "session_id",
        "conversation_id",
        "cache_info",
    ]:
        payload_data.pop(key, None)

    if request.reasoning_effort:
        payload_data["reasoning_effort"] = request.reasoning_effort
    elif request.thinking:
        payload_data["reasoning_effort"] = "medium"
    else:
        payload_data.pop("reasoning_effort", None)

    if payload_data.get("tools") is None:
        payload_data.pop("tools", None)
    if payload_data.get("tool_choice") is None:
        payload_data.pop("tool_choice", None)
    if stream:
        payload_data["stream"] = True
    return payload_data


class LlmProviderService:
    def __init__(self) -> None:
        self.openai_api_key: str | None = config.OPENAI_API_KEY
        self.openai_base_url: str = config.OPENAI_BASE_URL

        self.gemini_api_key: str | None = config.GEMINI_API_KEY
        self.ollama_base_url: str = config.OLLAMA_URL

        self.groq_api_key: str | None = config.GROQ_API_KEY
        self.groq_base_url: str = config.GROQ_BASE_URL

        self.openrouter_api_key: str | None = config.OPENROUTER_API_KEY
        self.openrouter_base_url: str = config.OPENROUTER_BASE_URL

        self.claude_api_key: str | None = config.CLAUDE_API_KEY
        self.claude_base_url: str = config.CLAUDE_BASE_URL

        self.gemini_client = None
        if genai and self.gemini_api_key:
            self.gemini_client = genai.Client(api_key=self.gemini_api_key)

    @log_latency()
    async def generate_ollama_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self.ollama_base_url}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}

        payload_data = _prepare_openai_compatible_payload(request, stream=False)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding request to local Ollama ({request.model})...")
                response = await client.post(
                    url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "Ollama error")
            logger.error(
                f"Ollama returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call Ollama: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

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
                contents.append(types.Content(role="tool", parts=[part]))
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
                        parts.append(
                            types.Part.from_function_call(
                                name=tc.function.name,
                                args=args,
                            )
                        )
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
            if response.function_calls:
                for fc in response.function_calls:
                    call_id = fc.id or f"call_{uuid.uuid4().hex}"
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

    @log_latency()
    async def generate_openai_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.openai_api_key:
            logger.error("OpenAI API key is not configured.")
            raise HTTPException(
                status_code=503, detail="OpenAI API key is not configured"
            )

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }

        payload_data = _prepare_openai_compatible_payload(request, stream=False)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(
                    f"Forwarding request to upstream OpenAI ({request.model})..."
                )
                response = await client.post(
                    self.openai_base_url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "Upstream LLM error")
            logger.error(
                f"Upstream API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call upstream LLM: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

    @log_latency()
    async def generate_groq_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.groq_api_key:
            logger.error("Groq API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Groq API key is not configured"
            )

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }

        payload_data = _prepare_openai_compatible_payload(request, stream=False)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding request to upstream Groq ({request.model})...")
                response = await client.post(
                    self.groq_base_url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "Groq LLM error")
            logger.error(
                f"Groq API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call Groq LLM: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

    @log_latency()
    async def generate_openrouter_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.openrouter_api_key:
            logger.error("OpenRouter API key is not configured.")
            raise HTTPException(
                status_code=503, detail="OpenRouter API key is not configured"
            )

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chrisindark/echo-gate",  # Optional but recommended by openrouter
            "X-Title": "Echo Gate",  # Optional
        }

        payload_data = _prepare_openai_compatible_payload(request, stream=False)

        try:
            async with httpx.AsyncClient() as client:
                logger.info(
                    f"Forwarding request to upstream OpenRouter ({request.model})..."
                )
                response = await client.post(
                    self.openrouter_base_url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, "OpenRouter LLM error")
            logger.error(
                f"OpenRouter API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to call OpenRouter LLM: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

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

    def generate_mock_response(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if request.tools and request.tool_choice != "none":
            target_fn_name = None
            if isinstance(request.tool_choice, dict):
                target_fn_name = request.tool_choice.get("function", {}).get("name")
            if not target_fn_name and request.tools:
                target_fn_name = request.tools[0].function.name

            mock_tool_call = ToolCall(
                id=f"call_{uuid.uuid4().hex}",
                type="function",
                function=FunctionCall(
                    name=target_fn_name or "mock_tool", arguments="{}"
                ),
            )
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant",
                            content=None,
                            tool_calls=[mock_tool_call],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )

        reasoning_content = (
            "Analyzing the user request step by step..."
            if (request.thinking or request.reasoning_effort)
            else None
        )
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=MOCK_RESPONSE_TEXT,
                        reasoning_content=reasoning_content,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            system_fingerprint="mock",
        )

    @log_latency()
    async def generate_completion(
        self, request: ChatCompletionRequest, service_name: str | None = None
    ) -> ChatCompletionResponse:
        service = (
            service_name or request.service_name or config.DEFAULT_LLM_SERVICE
        ).lower()
        if service in ["gemini", "google-genai"]:
            return await self.generate_gemini_completion(request)
        elif service == "openai":
            return await self.generate_openai_completion(request)
        elif service == "groq":
            return await self.generate_groq_completion(request)
        elif service == "openrouter":
            return await self.generate_openrouter_completion(request)
        elif service == "ollama":
            return await self.generate_ollama_completion(request)
        elif service in ["claude", "anthropic"]:
            return await self.generate_claude_completion(request)
        elif service == "mock":
            return self.generate_mock_response(request)
        else:
            logger.error(f"Unsupported LLM service: {service}")
            raise HTTPException(
                status_code=400, detail=f"Unsupported LLM service: {service}"
            )

    async def _stream_openai_compatible(
        self,
        url: str,
        headers: dict[str, str],
        payload_data: dict[str, Any],
        service_label: str,
    ) -> AsyncIterator[ChatCompletionChunk]:
        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding stream request to {service_label}...")
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
                            response, f"{service_label} error"
                        )
                        logger.error(
                            f"{service_label} returned HTTP error: {response.status_code} - {response.text}"
                        )
                        raise HTTPException(
                            status_code=response.status_code, detail=detail
                        )

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                if isinstance(chunk_data, dict):
                                    chunk_data.setdefault(
                                        "id", f"chatcmpl-{uuid.uuid4().hex}"
                                    )
                                    chunk_data.setdefault("created", int(time.time()))
                                    chunk_data.setdefault(
                                        "model",
                                        payload_data.get("model", "unknown"),
                                    )
                                    chunk_data.setdefault("choices", [])
                                    yield ChatCompletionChunk.model_validate(chunk_data)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to parse chunk from {service_label}: {e} - line: {line}"
                                )
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, f"{service_label} error")
            logger.error(
                f"{service_label} returned HTTP error: {e.response.status_code}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to stream from {service_label}: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")

    async def generate_ollama_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self.ollama_base_url}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        payload_data = _prepare_openai_compatible_payload(request, stream=True)
        async for chunk in self._stream_openai_compatible(
            url, headers, payload_data, f"local Ollama ({request.model})"
        ):
            yield chunk

    async def generate_openai_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if not self.openai_api_key:
            logger.error("OpenAI API key is not configured.")
            raise HTTPException(
                status_code=503, detail="OpenAI API key is not configured"
            )
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload_data = _prepare_openai_compatible_payload(request, stream=True)
        async for chunk in self._stream_openai_compatible(
            self.openai_base_url,
            headers,
            payload_data,
            f"upstream OpenAI ({request.model})",
        ):
            yield chunk

    async def generate_groq_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if not self.groq_api_key:
            logger.error("Groq API key is not configured.")
            raise HTTPException(
                status_code=503, detail="Groq API key is not configured"
            )
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload_data = _prepare_openai_compatible_payload(request, stream=True)
        async for chunk in self._stream_openai_compatible(
            self.groq_base_url,
            headers,
            payload_data,
            f"upstream Groq ({request.model})",
        ):
            yield chunk

    async def generate_openrouter_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        if not self.openrouter_api_key:
            logger.error("OpenRouter API key is not configured.")
            raise HTTPException(
                status_code=503, detail="OpenRouter API key is not configured"
            )
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chrisindark/echo-gate",
            "X-Title": "Echo Gate",
        }
        payload_data = _prepare_openai_compatible_payload(request, stream=True)
        async for chunk in self._stream_openai_compatible(
            self.openrouter_base_url,
            headers,
            payload_data,
            f"upstream OpenRouter ({request.model})",
        ):
            yield chunk

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
                if getattr(response, "function_calls", None):
                    function_calls = response.function_calls
                elif (
                    response.candidates
                    and response.candidates[0].content
                    and response.candidates[0].content.parts
                ):
                    for part in response.candidates[0].content.parts:
                        if getattr(part, "function_call", None):
                            function_calls.append(part.function_call)

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

    async def generate_mock_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_time = int(time.time())

        # 1. Role chunk
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
            system_fingerprint="mock",
        )

        if request.tools and request.tool_choice != "none":
            target_fn_name = None
            if isinstance(request.tool_choice, dict):
                target_fn_name = request.tool_choice.get("function", {}).get("name")
            if not target_fn_name and request.tools:
                target_fn_name = request.tools[0].function.name

            tool_id = f"call_{uuid.uuid4().hex}"
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            tool_calls=[
                                ToolCallChunk(
                                    index=0,
                                    id=tool_id,
                                    type="function",
                                    function=FunctionCallChunk(
                                        name=target_fn_name or "mock_tool",
                                        arguments="{}",
                                    ),
                                )
                            ]
                        ),
                        finish_reason=None,
                    )
                ],
                system_fingerprint="mock",
            )
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(),
                        finish_reason="tool_calls",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )
        else:
            if request.thinking or request.reasoning_effort:
                yield ChatCompletionChunk(
                    id=chunk_id,
                    created=created_time,
                    model=request.model,
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(
                                reasoning_content="Analyzing the user request step by step..."
                            ),
                            finish_reason=None,
                        )
                    ],
                    system_fingerprint="mock",
                )

            words = MOCK_RESPONSE_TEXT.split(" ")
            for i, word in enumerate(words):
                token = word + (" " if i < len(words) - 1 else "")
                yield ChatCompletionChunk(
                    id=chunk_id,
                    created=created_time,
                    model=request.model,
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(content=token),
                            finish_reason=None,
                        )
                    ],
                    system_fingerprint="mock",
                )
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )

    async def generate_stream(
        self, request: ChatCompletionRequest, service_name: str | None = None
    ) -> AsyncIterator[ChatCompletionChunk]:
        service = (
            service_name or request.service_name or config.DEFAULT_LLM_SERVICE
        ).lower()
        if service in ["gemini", "google-genai"]:
            async for chunk in self.generate_gemini_stream(request):
                yield chunk
        elif service == "openai":
            async for chunk in self.generate_openai_stream(request):
                yield chunk
        elif service == "groq":
            async for chunk in self.generate_groq_stream(request):
                yield chunk
        elif service == "openrouter":
            async for chunk in self.generate_openrouter_stream(request):
                yield chunk
        elif service == "ollama":
            async for chunk in self.generate_ollama_stream(request):
                yield chunk
        elif service in ["claude", "anthropic"]:
            async for chunk in self.generate_claude_stream(request):
                yield chunk
        elif service == "mock":
            async for chunk in self.generate_mock_stream(request):
                yield chunk
        else:
            logger.error(f"Unsupported LLM service: {service}")
            raise HTTPException(
                status_code=400, detail=f"Unsupported LLM service: {service}"
            )
