import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import errors, types

from app.core.config import config
from app.core.constants import MOCK_RESPONSE_TEXT
from app.core.logger import log_latency
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    FunctionCall,
    ToolCall,
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

        self.gemini_client = None
        if genai and self.gemini_api_key:
            self.gemini_client = genai.Client(api_key=self.gemini_api_key)

    @log_latency()
    async def generate_ollama_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self.ollama_base_url}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}

        payload_data = request.model_dump(exclude_unset=True)
        for key in [
            "service_name",
            "reasoning_effort",
            "user_id",
            "tenant_id",
            "session_id",
            "conversation_id",
            "cache_info",
        ]:
            payload_data.pop(key, None)
        if payload_data.get("tools") is None:
            payload_data.pop("tools", None)
        if payload_data.get("tool_choice") is None:
            payload_data.pop("tool_choice", None)

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

        config = types.GenerateContentConfig(**config_kwargs)

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
                    call_id = fc.id or f"call_{uuid.uuid4().hex[:12]}"
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
            content_text = response.text if response.text else None

            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant",
                            content=content_text,
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

        # Remove service_name and metadata fields before sending to OpenAI
        payload_data = request.model_dump(exclude_unset=True)
        for key in [
            "service_name",
            "reasoning_effort",
            "user_id",
            "tenant_id",
            "session_id",
            "conversation_id",
            "cache_info",
        ]:
            payload_data.pop(key, None)
        if payload_data.get("tools") is None:
            payload_data.pop("tools", None)
        if payload_data.get("tool_choice") is None:
            payload_data.pop("tool_choice", None)

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

        # Remove service_name and metadata fields before sending
        payload_data = request.model_dump(exclude_unset=True)
        for key in [
            "service_name",
            "reasoning_effort",
            "user_id",
            "tenant_id",
            "session_id",
            "conversation_id",
            "cache_info",
        ]:
            payload_data.pop(key, None)
        if payload_data.get("tools") is None:
            payload_data.pop("tools", None)
        if payload_data.get("tool_choice") is None:
            payload_data.pop("tool_choice", None)

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

        # Remove service_name and metadata fields before sending
        payload_data = request.model_dump(exclude_unset=True)
        for key in [
            "service_name",
            "reasoning_effort",
            "user_id",
            "tenant_id",
            "session_id",
            "conversation_id",
            "cache_info",
        ]:
            payload_data.pop(key, None)
        if payload_data.get("tools") is None:
            payload_data.pop("tools", None)
        if payload_data.get("tool_choice") is None:
            payload_data.pop("tool_choice", None)

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
                id=f"call_{uuid.uuid4().hex[:12]}",
                type="function",
                function=FunctionCall(
                    name=target_fn_name or "mock_tool", arguments="{}"
                ),
            )
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
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

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=MOCK_RESPONSE_TEXT,
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
        else:
            logger.error(f"Unsupported LLM service: {service}")
            raise HTTPException(
                status_code=400, detail=f"Unsupported LLM service: {service}"
            )
