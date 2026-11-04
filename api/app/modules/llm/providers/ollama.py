"""Ollama (local runtime) adapter.

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


class OllamaProvider(OpenAICompatibleProvider):
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
