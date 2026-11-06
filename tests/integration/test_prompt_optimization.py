"""Integration tests for Prompt Token Optimization Pipeline (Phase 5 / P3 Phase A).

Pins the contract:
- Request-level opt-in via body field `prompt_optimization: true` or `PromptOptimizationConfig`.
- Header-level opt-in via `X-EchoGate-Prompt-Optimization` / `X-Prompt-Optimization`.
- Selective strategy enablement via comma-separated header or config object.
- Response headers `X-EchoGate-Prompt-Optimization` and `X-EchoGate-Prompt-Savings` return metrics.
- Optimized messages are used for cache key generation so whitespace/format variants share cache entries.
- Streaming responses expose optimization headers.
- Fail-open behavior when optimization encounters an error.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import config
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
    Usage,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


class TestPromptOptimizationIntegration(unittest.TestCase):
    def setUp(self):
        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
            prompt_optimizer=PromptOptimizer(),
        )

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[llm_quota_guard] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_request_body_opt_in_sets_headers(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Response text"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You    are    an    assistant.   \n\n\n\n"},
                {"role": "user", "content": "Hello       world!    "},
            ],
            "prompt_optimization": True,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        savings = resp.headers.get(EchoGateHeaders.PROMPT_SAVINGS)
        self.assertIsNotNone(savings)
        self.assertIn("tokens=", savings)
        self.assertIn("chars=", savings)

        # Verify router received the compacted request
        call_args = self.router._generate_completion_impl.call_args[0]
        optimized_req: ChatCompletionRequest = call_args[0]
        self.assertEqual(
            optimized_req.messages[0].content, "You are an assistant."
        )
        self.assertEqual(optimized_req.messages[1].content, "Hello world!")

    def test_header_opt_in_sets_headers(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Answer"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "Query    with    extra    spaces   \n\n\n"},
            ],
        }

        resp = self.client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={EchoGateHeaders.PROMPT_OPTIMIZATION: "true"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        self.assertIn("tokens=", resp.headers.get(EchoGateHeaders.PROMPT_SAVINGS, ""))

    def test_explicit_opt_out_disables_optimization(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Answer"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "Do      not      touch!   \n\n\n"},
            ],
            "prompt_optimization": False,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(EchoGateHeaders.PROMPT_OPTIMIZATION, resp.headers)

        call_args = self.router._generate_completion_impl.call_args[0]
        req: ChatCompletionRequest = call_args[0]
        self.assertEqual(req.messages[0].content, "Do      not      touch!   \n\n\n")

    def test_streaming_response_carries_optimization_headers(self):
        chunk1 = ChatCompletionChunk(
            id="chatcmpl-stream",
            created=123456789,
            model="gpt-4o-mini",
            choices=[],
        )
        chunk2 = ChatCompletionChunk(
            id="chatcmpl-stream",
            created=123456789,
            model="gpt-4o-mini",
            choices=[],
        )

        async def fake_stream(*_args, **_kwargs):
            yield chunk1
            yield chunk2

        self.router._generate_stream_impl = fake_stream

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Stream    with    excess    spaces.  \n\n\n"},
            ],
            "prompt_optimization": True,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        self.assertIn("tokens=", resp.headers.get(EchoGateHeaders.PROMPT_SAVINGS, ""))

    def test_cache_key_equivalence_for_divergent_formatting(self):
        # Two requests: one with messy whitespace, one clean.
        # Both optimized will generate identical cache keys.
        req_raw = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System   instructions   \n\n\n"),
                ChatMessage(role="user", content="hello      world!   \n\n"),
            ],
            prompt_optimization=True,
        )

        req_clean = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System instructions"),
                ChatMessage(role="user", content="hello world!"),
            ],
            prompt_optimization=False,
        )

        opt_req, _ = self.router.prompt_optimizer.optimize(req_raw)
        key_raw = self.router._generate_cache_key(opt_req)
        key_clean = self.router._generate_cache_key(req_clean)
        self.assertEqual(key_raw, key_clean)

    def test_ccr_request_body_opt_in(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Answer"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
                {"role": "assistant", "content": "Answer 2"},
                {"role": "user", "content": "Question 3 (recent)"},
                {"role": "assistant", "content": "Answer 3 (recent)"},
                {"role": "user", "content": "Question 4 (active)"},
            ],
            "prompt_optimization": {
                "enabled": True,
                "ccr": True,
                "ccr_keep_last_n_turns": 2,
                "ccr_strategy": "drop",
                "whitespace": False,
                "session_dedup": False,
                "json_table": False,
            },
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        call_args = self.router._generate_completion_impl.call_args[0]
        optimized_req: ChatCompletionRequest = call_args[0]
        self.assertEqual(optimized_req.messages[0].content, "System prompt")
        self.assertIn("omitted for context reduction", optimized_req.messages[1].content)
        self.assertEqual(optimized_req.messages[-2].content, "Answer 3 (recent)")
        self.assertEqual(optimized_req.messages[-1].content, "Question 4 (active)")

    def test_ccr_header_opt_in(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Answer"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        detailed_answer = (
            "This is a detailed explanation of distributed architectures, consensus mechanisms, "
            "failover policies, replication groups, and network partitioning strategies. " * 3
        )
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "Question 1: Explain distributed systems architecture."},
                {"role": "assistant", "content": detailed_answer},
                {"role": "user", "content": "Question 2: Explain consensus mechanisms in full detail."},
                {"role": "assistant", "content": detailed_answer},
                {"role": "user", "content": "Question 3 (recent)"},
                {"role": "assistant", "content": "Answer 3 (recent)"},
                {"role": "user", "content": "Question 4 (active)"},
            ],
        }

        resp = self.client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={
                EchoGateHeaders.PROMPT_OPTIMIZATION: "ccr",
                EchoGateHeaders.PROMPT_OPTIMIZATION_CCR_TURNS: "2",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        call_args = self.router._generate_completion_impl.call_args[0]
        optimized_req: ChatCompletionRequest = call_args[0]
        self.assertEqual(optimized_req.messages[0].content, "System prompt")
        self.assertIn("[Context briefing", optimized_req.messages[1].content)
        self.assertEqual(optimized_req.messages[-1].content, "Question 4 (active)")

    def test_headroom_trim_request_body(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Answer"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            cache_info={"matched": False},
        )
        self.router._generate_completion_impl = AsyncMock(return_value=mock_response)

        long_content = "This is a detailed intermediate answer with multiple tokens. " * 20
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "Question 1 (to prune)"},
                {"role": "assistant", "content": long_content},
                {"role": "user", "content": "Question 2 (keep)"},
            ],
            "prompt_optimization": {
                "enabled": True,
                "headroom_trim": True,
                "headroom_target_tokens": 150,
                "headroom_buffer_tokens": 10,
                "whitespace": False,
                "session_dedup": False,
                "json_table": False,
            },
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PROMPT_OPTIMIZATION), "applied"
        )
        call_args = self.router._generate_completion_impl.call_args[0]
        optimized_req: ChatCompletionRequest = call_args[0]
        self.assertEqual(optimized_req.messages[0].content, "System prompt")
        self.assertEqual(optimized_req.messages[-1].content, "Question 2 (keep)")
        contents = [m.content for m in optimized_req.messages]
        self.assertNotIn("Question 1 (to prune)", contents)


if __name__ == "__main__":
    unittest.main()
