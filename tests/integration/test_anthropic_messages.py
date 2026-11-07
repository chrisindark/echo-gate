"""Integration tests for the Anthropic Messages Protocol Adapter (POST /api/v1/messages)."""

import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    Usage,
)


class TestAnthropicMessagesIntegration(unittest.TestCase):
    def setUp(self):
        self.router = AsyncMock()
        self.client = TestClient(app)

        async def dummy_quota():
            return None

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[llm_quota_guard] = dummy_quota

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_create_message_non_streaming(self):
        mock_chat_response = ChatCompletionResponse(
            id="chatcmpl-claude-test-123",
            object="chat.completion",
            created=1234567890,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Hello from Claude adapter!",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            cache_info={"matched": True},
        )
        self.router.generate_completion = AsyncMock(return_value=mock_chat_response)

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "system": "You are Claude.",
            "messages": [
                {"role": "user", "content": "Hi there!"},
            ],
        }

        resp = self.client.post("/api/v1/messages", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("anthropic-version"), "2023-06-01")
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "HIT")

        data = resp.json()
        self.assertEqual(data["id"], "msg_claude-test-123")
        self.assertEqual(data["type"], "message")
        self.assertEqual(data["role"], "assistant")
        self.assertEqual(data["model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(len(data["content"]), 1)
        self.assertEqual(data["content"][0]["type"], "text")
        self.assertEqual(data["content"][0]["text"], "Hello from Claude adapter!")
        self.assertEqual(data["stop_reason"], "end_turn")
        self.assertEqual(data["usage"]["input_tokens"], 20)
        self.assertEqual(data["usage"]["output_tokens"], 10)

        # Verify router received a valid ChatCompletionRequest
        call_args = self.router.generate_completion.call_args[0]
        chat_req: ChatCompletionRequest = call_args[0]
        self.assertEqual(chat_req.messages[0].role, "system")
        self.assertEqual(chat_req.messages[0].content, "You are Claude.")
        self.assertEqual(chat_req.messages[1].role, "user")
        self.assertEqual(chat_req.messages[1].content, "Hi there!")

    def test_v1_alias_endpoint(self):
        mock_chat_response = ChatCompletionResponse(
            id="msg_alias-test",
            object="chat.completion",
            created=1234567890,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Alias response",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            cache_info={"matched": False},
        )
        self.router.generate_completion = AsyncMock(return_value=mock_chat_response)

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "Test alias"},
            ],
        }

        resp = self.client.post("/v1/messages", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], "msg_alias-test")
        self.assertEqual(resp.json()["content"][0]["text"], "Alias response")

    def test_create_message_streaming(self):
        async def mock_stream(request, api_key=None):
            yield ChatCompletionChunk(
                id="chunk-1",
                object="chat.completion.chunk",
                created=123,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(content="Streaming"),
                        finish_reason=None,
                    )
                ],
            )
            yield ChatCompletionChunk(
                id="chunk-2",
                object="chat.completion.chunk",
                created=123,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(content=" response"),
                        finish_reason="stop",
                    )
                ],
            )

        self.router.generate_stream = mock_stream

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "stream": True,
            "messages": [
                {"role": "user", "content": "Stream me"},
            ],
        }

        resp = self.client.post("/api/v1/messages", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
        self.assertEqual(resp.headers.get("anthropic-version"), "2023-06-01")

        body_text = resp.text
        self.assertIn("event: message_start", body_text)
        self.assertIn("event: content_block_start", body_text)
        self.assertIn("event: content_block_delta", body_text)
        self.assertIn("Streaming", body_text)
        self.assertIn(" response", body_text)
        self.assertIn("event: content_block_stop", body_text)
        self.assertIn("event: message_delta", body_text)
        self.assertIn("event: message_stop", body_text)

    def test_header_prompt_optimization_integration(self):
        mock_chat_response = ChatCompletionResponse(
            id="chatcmpl-opt-test",
            object="chat.completion",
            created=1234567890,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Compacted answer",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=8, completion_tokens=4, total_tokens=12),
            cache_info={"matched": False},
        )
        self.router.generate_completion = AsyncMock(return_value=mock_chat_response)

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 512,
            "messages": [
                {
                    "role": "user",
                    "content": "A    prompt    with   many    spaces.  \n\n\n\n",
                },
            ],
        }
        headers = {
            EchoGateHeaders.PROMPT_OPTIMIZATION: "whitespace",
        }

        resp = self.client.post("/api/v1/messages", json=payload, headers=headers)
        self.assertEqual(resp.status_code, 200)
        call_args = self.router.generate_completion.call_args[0]
        chat_req: ChatCompletionRequest = call_args[0]
        self.assertIsNotNone(chat_req.prompt_optimization)
        self.assertTrue(chat_req.prompt_optimization.whitespace)


if __name__ == "__main__":
    unittest.main()
