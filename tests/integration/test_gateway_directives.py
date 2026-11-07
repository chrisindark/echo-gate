"""Integration tests for Per-Request Gateway Directives (P3 / Item 1).

Tests:
- Request-level directives via body: `cache_enabled`, `cache_ttl`, `retries`.
- Header-level directives: `X-EchoGate-Cache`, `X-EchoGate-Cache-TTL`, `X-EchoGate-Retries`.
- Cache bypass: skipping cache lookups and writes, returning X-Cache: BYPASS.
- Custom TTL application for Redis and Qdrant cache layers.
- Custom retry count applied to upstream provider calls.
- Anthropic Messages API parity.
- Instant cache preference skipped when caching is disabled.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    apply_request_directives,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


class TestGatewayDirectives(unittest.IsolatedAsyncioTestCase):
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

        async def dummy_quota():
            return None

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[llm_quota_guard] = dummy_quota
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_apply_request_directives_helper(self):
        """Test header parsing in apply_request_directives."""
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # 1. Cache bypass headers
        for bypass_val in ("bypass", "no-cache", "false", "off", "0", "disabled"):
            req.cache_enabled = None
            apply_request_directives(req, {"X-EchoGate-Cache": bypass_val})
            self.assertIs(req.cache_enabled, False, f"Failed for {bypass_val}")

        # 2. Cache enable headers
        for enable_val in ("true", "on", "1", "yes", "enabled"):
            req.cache_enabled = None
            apply_request_directives(req, {"X-EchoGate-Cache": enable_val})
            self.assertIs(req.cache_enabled, True, f"Failed for {enable_val}")

        # 3. Cache TTL header
        req.cache_ttl = None
        apply_request_directives(req, {"X-EchoGate-Cache-TTL": "3600"})
        self.assertEqual(req.cache_ttl, 3600)

        # 4. Retries header
        req.retries = None
        apply_request_directives(req, {"X-EchoGate-Retries": "4"})
        self.assertEqual(req.retries, 4)

    def test_cache_enabled_false_in_body_bypasses_cache_lookup_and_write(self):
        """When cache_enabled: false is in body, cache lookup and cache write are bypassed."""
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Direct reply"),
                    finish_reason="stop",
                )
            ],
            cache_info={"matched": False, "cache_hit": False},
        )

        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(mock_response, "openai", "gpt-4o-mini")
        )
        self.router._background_save_cache = AsyncMock()
        self.router._create_background_task = MagicMock()

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "cache_enabled": False,
        }

        response = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get(EchoGateHeaders.CACHE), "BYPASS")

        # Cache lookup was never called
        self.router._lookup_serving_cache.assert_not_called()
        # Provider was called directly
        self.router._call_provider_with_retry.assert_called_once()
        # Cache save task was not scheduled
        self.router._background_save_cache.assert_not_called()

    def test_header_cache_bypass_overrides_body(self):
        """Header X-EchoGate-Cache: bypass overrides body cache_enabled: true."""
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Fresh answer"),
                    finish_reason="stop",
                )
            ],
            cache_info={"matched": False, "cache_hit": False},
        )

        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(mock_response, "openai", "gpt-4o-mini")
        )
        self.router._create_background_task = MagicMock()

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello"}],
            "cache_enabled": True,  # Body requests cache, but header says bypass
        }

        response = self.client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={"X-EchoGate-Cache": "bypass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.router._lookup_serving_cache.assert_not_called()

    def test_retries_override_propagates_to_provider_call(self):
        """Custom retries in body or header is passed to _call_provider_with_retry."""
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
            cache_info={"matched": False, "cache_hit": False},
        )

        self.router._lookup_serving_cache = AsyncMock(return_value=(None, {}))
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(mock_response, "openai", "gpt-4o-mini")
        )
        self.router._create_background_task = MagicMock()

        # Body retries: 5
        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Retry test"}],
            "retries": 5,
        }
        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        call_kwargs = self.router._call_provider_with_retry.call_args.kwargs
        self.assertEqual(call_kwargs.get("max_retries"), 5)

        # Header retries: 2 overrides
        self.router._call_provider_with_retry.reset_mock()
        resp = self.client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={"X-EchoGate-Retries": "2"},
        )
        self.assertEqual(resp.status_code, 200)
        call_kwargs = self.router._call_provider_with_retry.call_args.kwargs
        self.assertEqual(call_kwargs.get("max_retries"), 2)

    def test_anthropic_directives_parity(self):
        """Anthropic Messages endpoint honors cache_enabled: false and custom headers."""
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Claude reply"),
                    finish_reason="stop",
                )
            ],
            cache_info={"matched": False, "cache_hit": False},
        )

        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(mock_response, "claude", "claude-3-5-sonnet-20241022")
        )
        self.router._create_background_task = MagicMock()

        anthropic_payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello Anthropic"}],
            "max_tokens": 100,
            "cache_enabled": False,
        }

        resp = self.client.post("/api/v1/messages", json=anthropic_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.router._lookup_serving_cache.assert_not_called()

    async def test_cache_ttl_override_in_save_to_qdrant_cache(self):
        """Custom cache_ttl overrides retention policy in save_to_qdrant_cache."""
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Dynamic data")],
            cache_ttl=180,
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-1",
            created=123,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="180s cache"),
                    finish_reason="stop",
                )
            ],
            cache_info={"matched": False, "cache_hit": False},
        )
        self.router.llm_cache_service = MagicMock()
        self.router.llm_cache_service.upsert_embedding = AsyncMock(return_value="pt-1")
        self.router.embedding_service = MagicMock()
        self.router.embedding_service.get_embedding_async = AsyncMock(return_value=[0.1] * 384)
        self.router.embedding_service.get_sparse_embedding = MagicMock(
            return_value={"indices": [1], "values": [1.0]}
        )
        self.router.intent_classifier_service = MagicMock()
        self.router.intent_classifier_service.classify_intent = AsyncMock(return_value=None)
        self.router.entity_extractor_service = MagicMock()
        from app.modules.qdrant.qdrant_schema import CacheScope

        self.router.entity_extractor_service.get_tags_and_max_scope.return_value = (
            [],
            CacheScope.GLOBAL,
        )

        point_id, ttl = await self.router.save_to_qdrant_cache(
            request=req,
            response=resp,
            save_to_qdrant=True,
        )
        self.assertEqual(ttl, 180)

    def test_instant_cache_hit_skipped_when_cache_disabled(self):
        """When cache_enabled is False, instant cache hit check is skipped."""
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=123456789,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Streamed reply"),
                    finish_reason="stop",
                )
            ],
            cache_info={"matched": False, "cache_hit": False},
        )

        self.router.get_instant_cache_hit = AsyncMock()

        async def fake_stream(*args, **kwargs):
            from app.modules.chat.chat_schema import ChatCompletionChunk, ChunkChoice, ChunkDelta
            yield (
                ChatCompletionChunk(
                    id="chunk-1",
                    created=123,
                    model="gpt-4o-mini",
                    choices=[ChunkChoice(index=0, delta=ChunkDelta(content="Hello"), finish_reason="stop")],
                ),
                "openai",
                "gpt-4o-mini",
            )

        self.router._stream_from_provider_with_fallback = fake_stream

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Stream me"}],
            "stream": True,
            "cache_enabled": False,
        }

        resp = self.client.post(
            "/api/v1/chat/completions",
            json=payload,
            headers={EchoGateHeaders.CACHE_PREFER: "instant"},
        )
        self.assertEqual(resp.status_code, 200)
        self.router.get_instant_cache_hit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
