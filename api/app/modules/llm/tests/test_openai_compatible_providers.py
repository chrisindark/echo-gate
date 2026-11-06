import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatMessage,
)
from app.modules.llm.llm_provider_service import LlmProviderService


class TestOpenAICompatibleProviders(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        self.mock_response_data = {
            "id": "chatcmpl-test-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello from mock upstream!",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 15,
                "total_tokens": 25,
            },
        }

    async def test_groq_missing_api_key_raises_503(self):
        provider = LlmProviderService()
        provider.groq_api_key = None
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_groq_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 503)

        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_groq_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_groq_completion_success(self):
        provider = LlmProviderService()
        provider.groq_api_key = "gsk_test"
        provider.groq_base_url = "https://api.groq.com/openai/v1/chat/completions"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_response_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await provider.generate_groq_completion(self.request)
            self.assertEqual(res.id, "chatcmpl-test-123")
            self.assertEqual(
                res.choices[0].message.content, "Hello from mock upstream!"
            )

    async def test_openai_missing_api_key_raises_503(self):
        provider = LlmProviderService()
        provider.openai_api_key = None
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_openai_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 503)

        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_openai_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_openai_completion_success(self):
        provider = LlmProviderService()
        provider.openai_api_key = "sk-test"
        provider.openai_base_url = "https://api.openai.com/v1/chat/completions"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_response_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await provider.generate_openai_completion(self.request)
            self.assertEqual(res.id, "chatcmpl-test-123")

    async def test_openrouter_missing_api_key_raises_503(self):
        provider = LlmProviderService()
        provider.openrouter_api_key = None
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_openrouter_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 503)

        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_openrouter_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_openrouter_completion_success(self):
        provider = LlmProviderService()
        provider.openrouter_api_key = "sk-or-test"
        provider.openrouter_base_url = "https://openrouter.ai/api/v1/chat/completions"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_response_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await provider.generate_openrouter_completion(self.request)
            self.assertEqual(res.id, "chatcmpl-test-123")

    async def test_ollama_missing_base_url_raises_502(self):
        provider = LlmProviderService()
        provider.ollama_base_url = ""
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_ollama_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_ollama_completion_success(self):
        provider = LlmProviderService()
        provider.ollama_base_url = "http://localhost:11434"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_response_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await provider.generate_ollama_completion(self.request)
            self.assertEqual(res.id, "chatcmpl-test-123")


if __name__ == "__main__":
    unittest.main()
