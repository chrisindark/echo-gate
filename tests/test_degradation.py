"""Router-level degradation tests: fail-open marking through the real pipeline.

These drive ``LlmRouterService.generate_completion`` / ``generate_stream`` rather
than the helpers in isolation, so the mapping from a cache failure to the
``X-Gateway-Degradation`` header is covered end to end.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from starlette.testclient import TestClient

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import circuit_breaker_registry
from app.core.config import config
from app.core.degradation import (
    DEGRADATION_CACHE_BYPASSED,
    DEGRADATION_NONE,
    DEGRADATION_PROVIDER_FALLBACK,
    cache_degradation_scope,
    is_cache_degraded,
    mark_cache_degraded,
    resolve_degradation_state,
)
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
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.qdrant.llm_cache_collection_service import (
    LlmCacheCollectionService,
)
from app.modules.redis.redis_service import RedisService


class _FailingRedisClient:
    async def get(self, key):
        raise ConnectionError("redis is down")

    async def delete(self, key):
        return None


class _FailingQdrantClient:
    def scroll(self, **kwargs):
        raise ConnectionError("qdrant is down")


def _response(content="hello", model="gpt-4o-mini") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content=content))
        ],
    )


def _chunk() -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chatcmpl-test",
        created=1,
        model="gpt-4o-mini",
        choices=[
            ChunkChoice(
                index=0,
                delta=ChunkDelta(role="assistant", content="hi"),
                finish_reason=None,
            )
        ],
    )


def _request(model="gpt-4o-mini", service="openai") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name=service,
        model=model,
        messages=[ChatMessage(role="user", content="hi")],
    )


def _router(provider, redis_service=None) -> LlmRouterService:
    return LlmRouterService(
        llm_provider_service=provider,
        embedding_service=MagicMock(),
        llm_cache_service=None,
        redis_service=redis_service or MagicMock(),
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(),
    )


def _failing_redis() -> RedisService:
    service = RedisService(redis_url="redis://127.0.0.1:1/0")
    service.redis = _FailingRedisClient()
    return service


class _SuccessProvider:
    async def generate_openai_completion(self, request):
        return _response()


class TestDegradationResolution(unittest.TestCase):
    def test_unmarked_request_is_full(self):
        with cache_degradation_scope():
            self.assertEqual(resolve_degradation_state(None), DEGRADATION_NONE)
            self.assertEqual(
                resolve_degradation_state(DEGRADATION_NONE), DEGRADATION_NONE
            )

    def test_cache_failure_marks_cache_bypassed(self):
        with cache_degradation_scope():
            mark_cache_degraded("test")
            self.assertTrue(is_cache_degraded())
            self.assertEqual(
                resolve_degradation_state("none"), DEGRADATION_CACHE_BYPASSED
            )

    def test_cache_hit_is_not_reported_as_bypassed(self):
        with cache_degradation_scope():
            mark_cache_degraded("test")
            self.assertEqual(
                resolve_degradation_state(None, served_from_cache=True),
                DEGRADATION_NONE,
            )

    def test_provider_fallback_dominates_cache_bypassed(self):
        with cache_degradation_scope():
            mark_cache_degraded("test")
            self.assertEqual(
                resolve_degradation_state(DEGRADATION_PROVIDER_FALLBACK),
                DEGRADATION_PROVIDER_FALLBACK,
            )

    def test_scope_isolation_between_requests(self):
        with cache_degradation_scope():
            mark_cache_degraded("failing request")
            self.assertTrue(is_cache_degraded())
        # A later request must start clean even on the same task/context.
        self.assertFalse(is_cache_degraded())


class TestCacheFailOpenMarking(unittest.IsolatedAsyncioTestCase):
    async def test_redis_read_failure_marks_cache_degraded(self):
        service = _failing_redis()
        with cache_degradation_scope():
            self.assertIsNone(await service.get("missing"))
            self.assertTrue(is_cache_degraded())

    async def test_qdrant_exact_failure_marks_cache_degraded(self):
        service = object.__new__(LlmCacheCollectionService)
        service.client_service = _FailingQdrantClient()
        service.collection_name = "embeddings_multivector"
        with cache_degradation_scope():
            self.assertEqual(service.search_exact("hash", "tenant"), (None, None))
            self.assertTrue(is_cache_degraded())


class TestRouterDegradationStates(unittest.IsolatedAsyncioTestCase):
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
                "FALLBACK_LLM_SERVICE",
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
                "LLM_MAX_RETRIES",
            )
        }
        config.USE_FALLBACK_LLM = "false"
        config.LLM_MAX_RETRIES = 0
        config.USE_QDRANT_EXACT_MATCHING = "false"
        config.USE_QDRANT_SEMANTIC_MATCHING = "false"
        config.USE_REDIS_EXACT_MATCHING = "false"
        config.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 100

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

    async def test_primary_success_reports_none(self):
        router = _router(_SuccessProvider())
        response = await router.generate_completion(_request())
        self.assertEqual(response._degradation_state, DEGRADATION_NONE)

    async def test_redis_failure_reports_cache_bypassed(self):
        config.USE_REDIS_EXACT_MATCHING = "true"
        router = _router(_SuccessProvider(), redis_service=_failing_redis())
        response = await router.generate_completion(_request())
        await router.shutdown()

        self.assertEqual(response.cache_info["source"], "llm")
        self.assertEqual(response._degradation_state, DEGRADATION_CACHE_BYPASSED)

    async def test_fallback_success_outranks_cache_bypassed(self):
        config.USE_REDIS_EXACT_MATCHING = "true"
        config.USE_FALLBACK_LLM = "true"
        config.FALLBACK_LLM_SERVICE = "google-genai"

        class _FallbackProvider:
            async def generate_openai_completion(self, request):
                raise HTTPException(status_code=503, detail="primary down")

            async def generate_gemini_completion(self, request):
                return _response(model="gemini-3.1-flash-lite")

        router = _router(_FallbackProvider(), redis_service=_failing_redis())
        response = await router.generate_completion(_request())
        await router.shutdown()

        self.assertEqual(response._degradation_state, DEGRADATION_PROVIDER_FALLBACK)

    async def test_stream_first_chunk_reports_cache_bypassed(self):
        config.USE_REDIS_EXACT_MATCHING = "true"

        class _StreamProvider:
            async def generate_stream(self, request, service_name=None):
                yield _chunk()
                yield _chunk()

        router = _router(_StreamProvider(), redis_service=_failing_redis())
        stream = router.generate_stream(_request())
        first = await stream.__anext__()
        await stream.aclose()
        await router.shutdown()

        self.assertEqual(first._degradation_state, DEGRADATION_CACHE_BYPASSED)


class TestDegradationHeaderWiring(unittest.TestCase):
    """The controller must surface the router's state, not a hardcoded value."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[llm_quota_guard] = lambda: None
        self.router = MagicMock(spec=LlmRouterService)
        app.dependency_overrides[get_llm_router_service] = lambda: self.router

    def tearDown(self):
        app.dependency_overrides.clear()

    def _post(self, **overrides):
        return self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hi"}],
                **overrides,
            },
        )

    def test_json_response_reports_cache_bypassed(self):
        response = _response()
        response._degradation_state = DEGRADATION_CACHE_BYPASSED
        self.router.generate_completion = AsyncMock(return_value=response)
        http_response = self._post()
        self.assertEqual(http_response.status_code, 200)
        self.assertEqual(
            http_response.headers["x-gateway-degradation"], DEGRADATION_CACHE_BYPASSED
        )

    def test_json_response_defaults_to_none_when_untagged(self):
        self.router.generate_completion = AsyncMock(return_value=_response())
        http_response = self._post()
        self.assertEqual(http_response.headers["x-gateway-degradation"], "none")

    def test_stream_reports_provider_fallback(self):
        async def stream(*args, **kwargs):
            chunk = _chunk()
            chunk._degradation_state = DEGRADATION_PROVIDER_FALLBACK
            yield chunk

        self.router.generate_stream = stream
        http_response = self._post(stream=True)
        self.assertEqual(http_response.status_code, 200)
        self.assertEqual(
            http_response.headers["x-gateway-degradation"],
            DEGRADATION_PROVIDER_FALLBACK,
        )


if __name__ == "__main__":
    unittest.main()
