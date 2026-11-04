"""OpenRouter adapter.

Extracted verbatim from the former single-file `LlmProviderService`. The methods
still operate on the dispatcher instance (``self``), which owns every provider
credential and client; only the owning class changed.
"""

import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from app.core.config import config
from app.core.logger import log_latency
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from app.modules.llm.providers.common import (
    extract_http_error_detail as _extract_http_error_detail,
)
from app.modules.llm.providers.common import (
    prepare_openai_compatible_payload as _prepare_openai_compatible_payload,
)
from app.modules.llm.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)


class OpenRouterProvider(OpenAICompatibleProvider):
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
