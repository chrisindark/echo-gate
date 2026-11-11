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

    async def test_provider_error_handling(self):
        import httpx

        provider = LlmProviderService()
        provider.openai_api_key = "sk-test"
        provider.groq_api_key = "gsk-test"
        provider.openrouter_api_key = "sk-or-test"
        provider.ollama_base_url = "http://localhost:11434"

        # HTTPStatusError
        dummy_req = httpx.Request("POST", "http://test")
        dummy_resp = httpx.Response(
            429, request=dummy_req, json={"error": {"message": "Rate limit"}}
        )
        http_err = httpx.HTTPStatusError("429", request=dummy_req, response=dummy_resp)

        mock_client = AsyncMock()
        mock_client.post.side_effect = http_err
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_openai_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 429)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_groq_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 429)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_openrouter_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 429)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_ollama_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 429)

        # Generic Exception -> 502
        mock_client.post.side_effect = RuntimeError("network fail")
        with patch("httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_openai_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 502)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_groq_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 502)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_openrouter_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 502)

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_ollama_completion(self.request)
            self.assertEqual(ctx.exception.status_code, 502)

    async def test_provider_streaming_streams(self):
        provider = LlmProviderService()
        provider.openai_api_key = "sk-test"
        provider.groq_api_key = "gsk-test"
        provider.openrouter_api_key = "sk-or-test"
        provider.ollama_base_url = "http://localhost:11434"

        from app.modules.chat.chat_schema import (
            ChatCompletionChunk,
            ChunkChoice,
            ChunkDelta,
        )

        sample_chunk = ChatCompletionChunk(
            id="c-1",
            created=100,
            model="gpt-4o",
            choices=[ChunkChoice(index=0, delta=ChunkDelta(content="hi"))],
        )

        async def fake_stream(*args, **kwargs):
            yield sample_chunk

        provider._stream_openai_compatible = fake_stream

        # Test streams yield chunks
        chunks = [c async for c in provider.generate_openai_stream(self.request)]
        self.assertEqual(len(chunks), 1)

        chunks_groq = [c async for c in provider.generate_groq_stream(self.request)]
        self.assertEqual(len(chunks_groq), 1)

        chunks_or = [c async for c in provider.generate_openrouter_stream(self.request)]
        self.assertEqual(len(chunks_or), 1)

        chunks_ollama = [c async for c in provider.generate_ollama_stream(self.request)]
        self.assertEqual(len(chunks_ollama), 1)


class TestCommonHelpersAndStreaming(unittest.IsolatedAsyncioTestCase):
    def test_extract_http_error_detail(self):
        import httpx

        from app.modules.llm.providers.common import extract_http_error_detail

        dummy_req = httpx.Request("POST", "http://test")

        # dict error message
        r1 = httpx.Response(
            400, request=dummy_req, json={"error": {"message": "invalid prompt"}}
        )
        self.assertEqual(
            extract_http_error_detail(r1, "Fallback"), "Fallback: invalid prompt"
        )

        # str error
        r2 = httpx.Response(
            400, request=dummy_req, json={"error": "simple string error"}
        )
        self.assertEqual(
            extract_http_error_detail(r2, "Fallback"), "Fallback: simple string error"
        )

        # message at top level
        r3 = httpx.Response(
            400, request=dummy_req, json={"message": "top level message"}
        )
        self.assertEqual(
            extract_http_error_detail(r3, "Fallback"), "Fallback: top level message"
        )

        # raw text
        r4 = httpx.Response(500, request=dummy_req, text="Internal Server Error")
        self.assertEqual(
            extract_http_error_detail(r4, "Fallback"), "Fallback: Internal Server Error"
        )

        # empty
        r5 = httpx.Response(500, request=dummy_req, text="")
        self.assertEqual(extract_http_error_detail(r5, "Fallback"), "Fallback")

    def test_prepare_openai_compatible_payload(self):
        from app.modules.llm.providers.common import prepare_openai_compatible_payload

        # Reasoning effort
        req1 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hi")],
            reasoning_effort="high",
        )
        p1 = prepare_openai_compatible_payload(req1, stream=True)
        self.assertEqual(p1["reasoning_effort"], "high")
        self.assertTrue(p1["stream"])

        # Thinking boolean
        req2 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hi")],
            thinking=True,
        )
        p2 = prepare_openai_compatible_payload(req2)
        self.assertEqual(p2["reasoning_effort"], "medium")

    async def test_stream_openai_compatible_aiter(self):
        from app.modules.llm.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider()

        # Mock stream lines
        class FakeResponse:
            status_code = 200

            async def aiter_lines(self):
                yield ""
                yield ": keepalive"
                yield 'data: {"id": "c-1", "created": 1, "model": "test", "choices": [{"index": 0, "delta": {"content": "ok"}}]}'
                yield "data: invalid json"
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeClient:
            def stream(self, *args, **kwargs):
                return FakeResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeClient()):
            chunks = []
            async for chunk in provider._stream_openai_compatible(
                "http://test", {}, {"model": "test"}, "service"
            ):
                chunks.append(chunk)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].id, "c-1")

        # Upstream status error >= 400
        class FakeErrorResponse:
            status_code = 401
            text = "Unauthorized"

            async def aread(self):
                pass

            def json(self):
                return {"message": "Invalid key"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeErrorClient:
            def stream(self, *args, **kwargs):
                return FakeErrorResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeErrorClient()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in provider._stream_openai_compatible(
                    "http://test", {}, {}, "service"
                ):
                    pass
            self.assertEqual(ctx.exception.status_code, 401)

        # Stream raises generic Exception -> 502
        class FakeCrashClient:
            def stream(self, *args, **kwargs):
                raise RuntimeError("Network dropped")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeCrashClient()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in provider._stream_openai_compatible(
                    "http://test", {}, {}, "service"
                ):
                    pass
            self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
