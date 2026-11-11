import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
)
from app.modules.llm.llm_provider_service import LlmProviderService


class TestLlmProviderServiceDispatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = LlmProviderService()

    def test_init_with_gemini_key(self):
        from app.core.config import config

        with (
            patch.object(config, "GEMINI_API_KEY", "fake_gemini_key"),
            patch("google.genai.Client"),
        ):
            svc = LlmProviderService()
            self.assertIsNotNone(svc.gemini_client)

    async def test_generate_completion_dispatch(self):
        dummy_resp = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="ok"))
            ],
        )

        self.provider.generate_gemini_completion = AsyncMock(return_value=dummy_resp)
        self.provider.generate_openai_completion = AsyncMock(return_value=dummy_resp)
        self.provider.generate_groq_completion = AsyncMock(return_value=dummy_resp)
        self.provider.generate_openrouter_completion = AsyncMock(
            return_value=dummy_resp
        )
        self.provider.generate_ollama_completion = AsyncMock(return_value=dummy_resp)
        self.provider.generate_claude_completion = AsyncMock(return_value=dummy_resp)
        self.provider.generate_mock_response = MagicMock(return_value=dummy_resp)

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # Gemini
        res = await self.provider.generate_completion(req, service_name="gemini")
        self.assertEqual(res, dummy_resp)
        self.provider.generate_gemini_completion.assert_awaited_once()

        # Google-genai alias
        self.provider.generate_gemini_completion.reset_mock()
        await self.provider.generate_completion(req, service_name="google-genai")
        self.provider.generate_gemini_completion.assert_awaited_once()

        # OpenAI
        await self.provider.generate_completion(req, service_name="openai")
        self.provider.generate_openai_completion.assert_awaited_once()

        # Groq
        await self.provider.generate_completion(req, service_name="groq")
        self.provider.generate_groq_completion.assert_awaited_once()

        # OpenRouter
        await self.provider.generate_completion(req, service_name="openrouter")
        self.provider.generate_openrouter_completion.assert_awaited_once()

        # Ollama
        await self.provider.generate_completion(req, service_name="ollama")
        self.provider.generate_ollama_completion.assert_awaited_once()

        # Claude / Anthropic
        await self.provider.generate_completion(req, service_name="claude")
        self.provider.generate_claude_completion.assert_awaited_once()

        self.provider.generate_claude_completion.reset_mock()
        await self.provider.generate_completion(req, service_name="anthropic")
        self.provider.generate_claude_completion.assert_awaited_once()

        # Mock
        await self.provider.generate_completion(req, service_name="mock")
        self.provider.generate_mock_response.assert_called_once()

        # Unsupported service raises HTTPException(400)
        with self.assertRaises(HTTPException) as ctx:
            await self.provider.generate_completion(req, service_name="unknown_service")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_generate_stream_dispatch(self):
        dummy_chunk = ChatCompletionChunk(id="c1", created=1, model="m", choices=[])

        async def fake_stream(*args, **kwargs):
            yield dummy_chunk

        self.provider.generate_gemini_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_openai_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_groq_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_openrouter_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_ollama_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_claude_stream = MagicMock(side_effect=fake_stream)
        self.provider.generate_mock_stream = MagicMock(side_effect=fake_stream)

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )

        for svc in [
            "gemini",
            "google-genai",
            "openai",
            "groq",
            "openrouter",
            "ollama",
            "claude",
            "anthropic",
            "mock",
        ]:
            chunks = []
            async for chunk in self.provider.generate_stream(req, service_name=svc):
                chunks.append(chunk)
            self.assertEqual(len(chunks), 1)

        # Unsupported stream raises HTTPException(400)
        with self.assertRaises(HTTPException) as ctx:
            async for _ in self.provider.generate_stream(
                req, service_name="invalid_stream"
            ):
                pass
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
