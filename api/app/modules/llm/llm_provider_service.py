"""Provider dispatch façade for every supported upstream LLM.

Each upstream lives in its own adapter module under
``app.modules.llm.providers`` (``openai``, ``groq``, ``openrouter``, ``ollama``,
``gemini``, ``claude``, ``mock``). This module keeps only what is genuinely
cross-provider: credential/client construction and the ``service_name`` →
adapter dispatch used by ``LlmRouterService``.

Provider-specific behaviour must be added to the owning adapter, not here.
"""

import logging
from collections.abc import AsyncIterator

from fastapi import HTTPException
from google import genai

from app.core.config import config
from app.core.logger import log_latency
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from app.modules.llm.providers.claude import ClaudeProvider
from app.modules.llm.providers.gemini import GeminiProvider
from app.modules.llm.providers.groq import GroqProvider
from app.modules.llm.providers.mock import MockProvider
from app.modules.llm.providers.ollama import OllamaProvider
from app.modules.llm.providers.openai import OpenAIProvider
from app.modules.llm.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


class LlmProviderService(
    OpenAIProvider,
    GroqProvider,
    OpenRouterProvider,
    OllamaProvider,
    GeminiProvider,
    ClaudeProvider,
    MockProvider,
):
    """Facade over the per-provider adapters, keyed by ``service_name``."""

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
