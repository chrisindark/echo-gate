"""Multimodal content preservation across ingress, cache decisions, and providers."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.anthropic.anthropic_adapter import normalize_anthropic_to_chat_request
from app.modules.anthropic.anthropic_schema import (
    AnthropicMessage,
    AnthropicMessagesRequest,
)
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm.providers.claude import ClaudeProvider
from app.modules.llm.providers.common import prepare_openai_compatible_payload
from app.modules.llm.providers.gemini import GeminiProvider
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer

IMAGE_BLOCK = {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
}
DOCUMENT_BLOCK = {
    "type": "document",
    "source": {
        "type": "base64",
        "media_type": "application/pdf",
        "data": "QkJCQg==",
    },
}


def _openai_image_request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name="openai",
        model="gpt-4o-mini",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "describe"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,AAAA",
                            "detail": "low",
                        },
                    },
                    {"type": "text", "text": "briefly"},
                ],
            )
        ],
    )


def _anthropic_chat_request() -> ChatCompletionRequest:
    incoming = AnthropicMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        max_tokens=256,
        messages=[
            AnthropicMessage(
                role="user",
                content=[
                    {"type": "text", "text": "compare"},
                    IMAGE_BLOCK,
                    DOCUMENT_BLOCK,
                ],
            )
        ],
    )
    return normalize_anthropic_to_chat_request(incoming)


class TestMultimodalSchemaAndProviders(unittest.TestCase):
    def test_content_array_keeps_order_and_folds_text(self):
        request = _openai_image_request()
        message = request.messages[0]

        self.assertEqual(message.content, "describe\nbriefly")
        self.assertEqual(
            [part["type"] for part in message.content_parts],
            ["text", "image_url", "text"],
        )
        self.assertTrue(message.has_non_text_content)
        self.assertTrue(request.has_non_text_content)
        self.assertTrue(request.cache_bypassed)

    def test_text_only_request_keeps_legacy_path(self):
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )
        self.assertFalse(request.has_non_text_content)
        self.assertFalse(request.cache_bypassed)
        self.assertIsNone(request.messages[0].content_parts)

    def test_explicit_opt_in_enables_cache_for_non_text(self):
        request = _openai_image_request()
        request.cache_enabled = True
        self.assertFalse(request.cache_bypassed)

    def test_unknown_block_type_is_rejected(self):
        with self.assertRaises(ValueError):
            ChatMessage(
                role="user",
                content=[{"type": "audio", "data": "..."}],
            )

    def test_openai_compatible_payload_forwards_parts(self):
        payload = prepare_openai_compatible_payload(_openai_image_request())
        self.assertEqual(
            payload["messages"][0]["content"],
            [
                {"type": "text", "text": "describe"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,AAAA",
                        "detail": "low",
                    },
                },
                {"type": "text", "text": "briefly"},
            ],
        )

    def test_anthropic_ingress_preserves_image_and_document(self):
        chat_request = _anthropic_chat_request()
        message = chat_request.messages[0]

        self.assertEqual(message.content, "compare")
        self.assertEqual(
            [part["type"] for part in message.content_parts],
            ["text", "image", "document"],
        )
        self.assertTrue(chat_request.cache_bypassed)

    def test_claude_payload_forwards_image_url_in_order(self):
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.claude_base_url = "https://api.anthropic.com/v1"
        provider.claude_api_key = "test-key"
        _, _, payload = provider._prepare_claude_payload(_openai_image_request())

        self.assertEqual(
            payload["messages"][0]["content"],
            [
                {"type": "text", "text": "describe"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAA",
                    },
                },
                {"type": "text", "text": "briefly"},
            ],
        )

    def test_claude_payload_round_trips_anthropic_blocks(self):
        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider.claude_base_url = "https://api.anthropic.com/v1"
        provider.claude_api_key = "test-key"
        _, _, payload = provider._prepare_claude_payload(_anthropic_chat_request())

        self.assertEqual(payload["messages"][0]["content"][0]["text"], "compare")
        self.assertEqual(payload["messages"][0]["content"][1], IMAGE_BLOCK)
        self.assertEqual(payload["messages"][0]["content"][2], DOCUMENT_BLOCK)

    def test_gemini_payload_forwards_image_block(self):
        provider = GeminiProvider.__new__(GeminiProvider)
        contents, _ = provider._prepare_gemini_payload(_anthropic_chat_request())
        parts = contents[0].parts

        self.assertEqual(parts[0].text, "compare")
        self.assertEqual(parts[1].inline_data.mime_type, "image/png")
        self.assertEqual(parts[1].inline_data.data, b"\x00\x00\x00")
        self.assertEqual(parts[2].inline_data.mime_type, "application/pdf")

    def test_exact_cache_key_includes_non_text_digest(self):
        router = LlmRouterService.__new__(LlmRouterService)
        request = _openai_image_request()
        key = router._generate_cache_key(request)
        self.assertEqual(key, router._generate_cache_key(request.model_copy(deep=True)))

        other = request.model_copy(deep=True)
        other.messages[0].content_parts[1]["image_url"]["url"] = (
            "data:image/png;base64,BBBB"
        )
        self.assertNotEqual(key, router._generate_cache_key(other))

    def test_semantic_cache_is_skipped_for_non_text(self):
        router = LlmRouterService.__new__(LlmRouterService)
        result = asyncio.run(router.evaluate_semantic_cache(_openai_image_request()))
        self.assertEqual(result, {})

    def test_prompt_optimizer_leaves_multimodal_messages_untouched(self):
        request = _openai_image_request()
        request.prompt_optimization = True
        optimized, result = PromptOptimizer().optimize(request)
        self.assertIs(optimized, request)
        self.assertFalse(result.applied)


class TestMultimodalHttpPaths(unittest.TestCase):
    def setUp(self):
        self.router = MagicMock(spec=LlmRouterService)
        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[llm_quota_guard] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    @staticmethod
    def _response() -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id="chatcmpl-mm-1",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="A cat"),
                    finish_reason="stop",
                )
            ],
        )

    @staticmethod
    async def _chunks(*args, **kwargs):
        yield ChatCompletionChunk(
            id="chatcmpl-mm-stream",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
        )
        yield ChatCompletionChunk(
            id="chatcmpl-mm-stream",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                ChunkChoice(
                    index=0, delta=ChunkDelta(content="A cat"), finish_reason="stop"
                )
            ],
        )

    def test_openai_image_request_returns_bypass_and_keeps_parts(self):
        self.router.generate_completion = AsyncMock(return_value=self._response())
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                    ],
                }
            ],
        }

        response = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[EchoGateHeaders.CACHE], "BYPASS")
        request = self.router.generate_completion.call_args[0][0]
        self.assertEqual(
            [part["type"] for part in request.messages[0].content_parts],
            ["text", "image_url"],
        )

    def test_openai_image_stream_returns_bypass_and_keeps_parts(self):
        self.router.generate_stream = MagicMock(side_effect=self._chunks)
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                    ],
                }
            ],
        }

        response = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[EchoGateHeaders.CACHE], "BYPASS")
        self.assertIn("[DONE]", response.text)
        request = self.router.generate_stream.call_args[0][0]
        self.assertEqual(
            [part["type"] for part in request.messages[0].content_parts],
            ["text", "image_url"],
        )

    def test_anthropic_image_request_returns_bypass_and_keeps_parts(self):
        self.router.generate_completion = AsyncMock(return_value=self._response())
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "compare"},
                        IMAGE_BLOCK,
                        DOCUMENT_BLOCK,
                    ],
                }
            ],
        }

        response = self.client.post("/api/v1/messages", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[EchoGateHeaders.CACHE], "BYPASS")
        request = self.router.generate_completion.call_args[0][0]
        self.assertEqual(
            [part["type"] for part in request.messages[0].content_parts],
            ["text", "image", "document"],
        )

    def test_anthropic_image_stream_returns_bypass(self):
        self.router.generate_stream = MagicMock(side_effect=self._chunks)
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "compare"},
                        IMAGE_BLOCK,
                    ],
                }
            ],
        }

        response = self.client.post("/api/v1/messages", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[EchoGateHeaders.CACHE], "BYPASS")
        request = self.router.generate_stream.call_args[0][0]
        self.assertEqual(
            [part["type"] for part in request.messages[0].content_parts],
            ["text", "image"],
        )

    def test_unknown_content_block_is_rejected_over_http(self):
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": [{"type": "audio", "data": "..."}]}
            ],
        }
        response = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 422)
