import logging
import time
import uuid

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
    Usage,
)

logger = logging.getLogger(__name__)


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
        if "service_name" in payload_data:
            del payload_data["service_name"]

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
            logger.error(
                f"Ollama returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(
                status_code=e.response.status_code, detail="Ollama error"
            )
        except Exception as e:
            logger.error(f"Failed to call Ollama: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

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

        contents = []
        system_instruction = None
        for msg in request.messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "user" if msg.role == "user" else "model"
                contents.append(
                    types.Content(
                        role=role, parts=[types.Part.from_text(text=msg.content)]
                    )
                )

        config_kwargs = {}
        if request.temperature is not None:
            config_kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            config_kwargs["top_p"] = request.top_p
        if request.max_tokens is not None:
            config_kwargs["max_output_tokens"] = request.max_tokens
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if request.response_format:
            response_format_type = request.response_format.get("type")
            if response_format_type == "json_object":
                config_kwargs["response_mime_type"] = "application/json"
            elif response_format_type == "json_schema":
                config_kwargs["response_mime_type"] = "application/json"
                schema = request.response_format.get("json_schema", {}).get("schema")
                if schema:
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

            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant", content=response.text or ""
                        ),
                        finish_reason="stop",
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
            raise HTTPException(status_code=502, detail="Bad Gateway")

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

        # Remove service_name before sending to OpenAI
        payload_data = request.model_dump(exclude_unset=True)
        if "service_name" in payload_data:
            del payload_data["service_name"]

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
            logger.error(
                f"Upstream API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(
                status_code=e.response.status_code, detail="Upstream LLM error"
            )
        except Exception as e:
            logger.error(f"Failed to call upstream LLM: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

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

        # Remove service_name before sending
        payload_data = request.model_dump(exclude_unset=True)
        if "service_name" in payload_data:
            del payload_data["service_name"]

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
            logger.error(
                f"Groq API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(
                status_code=e.response.status_code, detail="Groq LLM error"
            )
        except Exception as e:
            logger.error(f"Failed to call Groq LLM: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

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

        # Remove service_name before sending
        payload_data = request.model_dump(exclude_unset=True)
        if "service_name" in payload_data:
            del payload_data["service_name"]

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
            logger.error(
                f"OpenRouter API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(
                status_code=e.response.status_code, detail="OpenRouter LLM error"
            )
        except Exception as e:
            logger.error(f"Failed to call OpenRouter LLM: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")

    def generate_mock_response(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
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
