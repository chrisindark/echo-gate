"""Cache write failure isolation tests.

Core invariant: cache write failures must never affect the client response.
If Redis/Qdrant fail during background cache writes, the provider response must
still be returned successfully and the error must be logged/swallowed.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import circuit_breaker_registry
from app.core.config import config
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
)
from app.modules.llm.llm_router_service import LlmRouterService


def _request(model="gpt-4o-mini", service="openai") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name=service,
        model=model,
        messages=[ChatMessage(role="user", content="hello")],
    )


def _response(model="gpt-4o-mini") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content="world"))
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    )


def _router(provider, redis_service=None, cache_service=None) -> LlmRouterService:
    if redis_service is None:
        redis_service = AsyncMock()
    if cache_service is None:
        cache_service = MagicMock()
    return LlmRouterService(
        llm_provider_service=provider,
        embedding_service=MagicMock(),
        llm_cache_service=cache_service,
        redis_service=redis_service,
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(),
    )


class _SuccessProvider:
    def __init__(self):
        self.calls = 0

    async def generate_openai_completion(self, request):
        self.calls += 1
        return _response()


class TestCacheWriteFailureIsolation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()
        self._saved = {
            name: getattr(config, name)
            for name in (
                "USE_FALLBACK_LLM",
                "USE_REDIS_EXACT_MATCHING",
                "USE_QDRANT_EXACT_MATCHING",
                "USE_QDRANT_SEMANTIC_MATCHING",
                "CACHE_MODE",
                "LLM_MAX_RETRIES",
                "GLOBAL_MAX_CONCURRENCY",
            )
        }
        config.USE_FALLBACK_LLM = "false"
        config.USE_REDIS_EXACT_MATCHING = "true"
        config.USE_QDRANT_EXACT_MATCHING = "true"
        config.USE_QDRANT_SEMANTIC_MATCHING = "true"
        config.CACHE_MODE = "active"
        config.LLM_MAX_RETRIES = 0

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

    async def test_redis_write_failure_does_not_break_response(self):
        provider = _SuccessProvider()
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=Exception("Redis connection failed"))
        cache_service = MagicMock()
        cache_service.search_exact = MagicMock(return_value=(None, None))
        cache_service.search = MagicMock(return_value=[])
        router = _router(provider, redis_service=redis, cache_service=cache_service)

        response, service, _ = await router._call_provider_with_retry(
            _request(), "openai"
        )
        self.assertIsInstance(response, ChatCompletionResponse)
        self.assertEqual(response.choices[0].message.content, "world")

    async def test_qdrant_write_failure_does_not_break_response(self):
        provider = _SuccessProvider()
        redis = AsyncMock()
        cache_service = MagicMock()
        cache_service.search_exact = MagicMock(return_value=(None, None))
        cache_service.search = MagicMock(return_value=[])
        cache_service.upsert_points = MagicMock(side_effect=Exception("Qdrant down"))
        router = _router(provider, redis_service=redis, cache_service=cache_service)

        response, service, _ = await router._call_provider_with_retry(
            _request(), "openai"
        )
        self.assertIsInstance(response, ChatCompletionResponse)
        self.assertEqual(response.choices[0].message.content, "world")

    async def test_background_save_cache_swallow_exceptions(self):
        provider = _SuccessProvider()
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=Exception("Redis boom"))
        cache_service = MagicMock()
        cache_service.upsert_points = MagicMock(side_effect=Exception("Qdrant boom"))
        router = _router(provider, redis_service=redis, cache_service=cache_service)

        resp = _response()
        # _background_save_cache must not raise
        await router._background_save_cache(
            request=_request(),
            response=resp,
            exact_hash="abc123",
            service_name="openai",
            model="gpt-4o-mini",
            intent=None,
            time_sensitivity=0.0,
            force=True,
        )

    async def test_save_to_redis_cache_handles_errors_gracefully(self):
        provider = _SuccessProvider()
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=Exception("Connection refused"))
        cache_service = MagicMock()
        router = _router(provider, redis_service=redis, cache_service=cache_service)

        resp = _response()
        # Should not raise
        await router._save_to_redis_cache(
            exact_hash="hash123",
            response=resp,
            log_message="test",
            ttl=3600,
            save_to_redis=True,
        )

    async def test_save_to_qdrant_cache_handles_errors_gracefully(self):
        provider = _SuccessProvider()
        redis = AsyncMock()
        cache_service = MagicMock()
        cache_service.upsert_points = MagicMock(side_effect=Exception("Timeout"))
        router = _router(provider, redis_service=redis, cache_service=cache_service)
        router.embedding_service = MagicMock()
        router.embedding_service.model_name = "test-model"
        router.embedding_service.get_embedding = AsyncMock(return_value=[0.1] * 384)
        router.embedding_service.get_sparse_embedding = AsyncMock(return_value={})
        router.reranker_service = MagicMock()

        resp = _response()
        try:
            result = await router.save_to_qdrant_cache(
                request=_request(),
                response=resp,
                service_name="openai",
                model="gpt-4o-mini",
                save_to_qdrant=True,
                force=True,
            )
            # Should return (None, None) or handle gracefully
            self.assertIsInstance(result, tuple)
        except Exception as e:
            self.fail(f"save_to_qdrant_cache raised exception: {e}")


if __name__ == "__main__":
    unittest.main()
