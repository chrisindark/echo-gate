"""Router-level circuit breaker and bulkhead tests.

These drive ``LlmRouterService._call_provider_with_retry`` instead of the helpers
in isolation, which is how the HALF_OPEN probe-leak regression was found.
"""

import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import (
    CircuitBreakerOpenException,
    CircuitBreakerState,
    circuit_breaker_registry,
)
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
        messages=[ChatMessage(role="user", content="hi")],
    )


def _response(model="gpt-4o-mini") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content="hello"))
        ],
    )


def _router(provider) -> LlmRouterService:
    return LlmRouterService(
        llm_provider_service=provider,
        embedding_service=MagicMock(),
        llm_cache_service=None,
        redis_service=MagicMock(),
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(),
    )


class _BoomProvider:
    def __init__(self, status_code=503):
        self.calls = 0
        self.status_code = status_code

    async def generate_openai_completion(self, request):
        self.calls += 1
        raise HTTPException(status_code=self.status_code, detail="upstream exploded")


class _SuccessProvider:
    def __init__(self):
        self.calls = 0

    async def generate_openai_completion(self, request):
        self.calls += 1
        return _response()


class TestRouterCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()
        self._saved = {
            name: getattr(config, name)
            for name in (
                "USE_FALLBACK_LLM",
                "USE_REDIS_EXACT_MATCHING",
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
                "GLOBAL_MAX_CONCURRENCY",
                "BULKHEAD_ACQUIRE_TIMEOUT_SECONDS",
                "LLM_MAX_RETRIES",
            )
        }
        config.USE_FALLBACK_LLM = "false"
        config.USE_REDIS_EXACT_MATCHING = "false"
        config.LLM_MAX_RETRIES = 0
        config.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 2

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

    async def test_open_breaker_fast_fails_without_calling_upstream(self):
        provider = _BoomProvider()
        router = _router(provider)
        for _ in range(2):
            with self.assertRaises(HTTPException):
                await router._call_provider_with_retry(_request(), "openai")

        calls_before = provider.calls
        with self.assertRaises(CircuitBreakerOpenException) as ctx:
            await router._call_provider_with_retry(_request(), "openai")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(provider.calls, calls_before, "open provider was still called")

    async def test_bulkhead_shed_releases_half_open_probe(self):
        """Regression: a shed request must not strand the HALF_OPEN probe permit."""
        provider = _SuccessProvider()
        breaker = circuit_breaker_registry.get_breaker("openai")
        breaker._state = CircuitBreakerState.HALF_OPEN
        breaker._half_open_probe_in_flight = False

        config.GLOBAL_MAX_CONCURRENCY = 1
        config.BULKHEAD_ACQUIRE_TIMEOUT_SECONDS = 0.0
        async with bulkhead_mod.bulkhead_manager.acquire("another-provider"):
            with self.assertRaises(bulkhead_mod.BulkheadLimitExceededException):
                await _router(provider)._call_provider_with_retry(_request(), "openai")

        self.assertEqual(provider.calls, 0)
        self.assertFalse(
            breaker._half_open_probe_in_flight,
            "probe permit leaked: the provider would be skipped until restart",
        )
        # The provider must be reachable again on the next request.
        _, service, _ = await _router(provider)._call_provider_with_retry(
            _request(), "openai"
        )
        self.assertEqual(service, "openai")
        self.assertEqual(breaker.state, CircuitBreakerState.HALF_OPEN)


if __name__ == "__main__":
    unittest.main()
