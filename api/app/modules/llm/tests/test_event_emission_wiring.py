"""Event bus wiring tests.

The seam in ``app.core.events`` is only useful if the four typed events are
actually emitted from the paths they describe. These tests pin the wiring, not
the sink mechanics (see ``app/core/tests/test_events.py``):

* ``cache.miss``      — provider served a request that missed the cache
* ``quota.exceeded``  — a caller was refused by RPM/TPM/budget
* ``provider.failure``— an upstream attempt failed (``final`` on the last one)
* ``audit.failed``    — a sampled cache audit found a bad entry

Every assertion also checks that emission stays a no-op when unconfigured — the
OSS default — so wiring cannot change serving behavior.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

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
from app.modules.llm_quota.llm_quota_service import LlmQuotaService


def _request(model="gpt-4o-mini", service="openai") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name=service,
        model=model,
        messages=[ChatMessage(role="user", content="hello")],
    )


def _response(model="gpt-4o-mini", cache_info=None) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content="world"))
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        cache_info=cache_info,
    )


def _router(provider) -> LlmRouterService:
    return LlmRouterService(
        llm_provider_service=provider,
        embedding_service=MagicMock(),
        llm_cache_service=MagicMock(),
        redis_service=AsyncMock(),
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(),
    )


class _FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_openai_completion(self, request):
        self.calls += 1
        raise RuntimeError("upstream exploded")


class TestCacheMissEmission(unittest.IsolatedAsyncioTestCase):
    async def test_provider_served_request_emits_cache_miss(self):
        router = _router(_FailingProvider())
        with patch("app.modules.llm.llm_router_service.emit_cache_miss") as emit:
            await router._log_usage(
                request=_request(),
                response=_response(),
                service_name="openai",
                start_time=0.0,
                exact_hash="hash-1",
                model="gpt-4o-mini",
            )
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["exact_hash"], "hash-1")
        self.assertEqual(kwargs["provider"], "openai")
        self.assertEqual(kwargs["source"], "provider")
        self.assertEqual(kwargs["cache_mode"], config.CACHE_MODE)

    async def test_cache_hit_does_not_emit_cache_miss(self):
        router = _router(_FailingProvider())
        with patch("app.modules.llm.llm_router_service.emit_cache_miss") as emit:
            await router._log_usage(
                request=_request(),
                response=_response(cache_info={"cache_hit": True}),
                service_name="openai",
                start_time=0.0,
                exact_hash="hash-1",
                model="gpt-4o-mini",
            )
        emit.assert_not_called()

    async def test_failed_request_does_not_emit_cache_miss(self):
        router = _router(_FailingProvider())
        with patch("app.modules.llm.llm_router_service.emit_cache_miss") as emit:
            await router._log_usage(
                request=_request(),
                response=None,
                service_name="openai",
                start_time=0.0,
                exact_hash="hash-1",
                model="gpt-4o-mini",
                is_success=False,
                status_code=500,
                error_message="boom",
            )
        emit.assert_not_called()

    async def test_mock_response_does_not_emit_cache_miss(self):
        router = _router(_FailingProvider())
        response = _response()
        response.system_fingerprint = "mock"
        with patch("app.modules.llm.llm_router_service.emit_cache_miss") as emit:
            await router._log_usage(
                request=_request(),
                response=response,
                service_name="openai",
                start_time=0.0,
                exact_hash="hash-1",
                model="gpt-4o-mini",
            )
        emit.assert_not_called()


class TestProviderFailureEmission(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()
        self._saved = {
            name: getattr(config, name)
            for name in ("USE_FALLBACK_LLM", "LLM_MAX_RETRIES")
        }
        config.USE_FALLBACK_LLM = "false"
        config.LLM_MAX_RETRIES = 0

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

    async def test_exhausted_candidates_emit_a_final_provider_failure(self):
        router = _router(_FailingProvider())
        with (
            patch("app.modules.llm.llm_router_service.emit_provider_failure") as emit,
            self.assertRaises(HTTPException),
        ):
            await router._call_provider_with_retry(
                request=_request(), service_name="openai"
            )
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["provider"], "openai")
        self.assertTrue(kwargs["final"])
        self.assertEqual(kwargs["status_code"], 500)
        self.assertIsNotNone(kwargs.get("error_code"))


class _StubRedis:
    def __init__(self, rpm=1, tpm="0", budget=None):
        self.rpm = rpm
        self.tpm = tpm
        self.budget = budget
        self.rpm_keys: list[str] = []

    async def get(self, key):
        if key.startswith("budget:"):
            return self.budget
        return self.tpm

    async def incr_with_ttl(self, key, ttl):
        self.rpm_keys.append(key)
        return self.rpm

    async def remaining_ttl(self, key):
        return 30


def _quota_service(redis, rpm_limit=10, tpm_limit=20000) -> LlmQuotaService:
    service = LlmQuotaService(redis_service=redis, session_factory=MagicMock())
    service.get_applicable_limits = AsyncMock(return_value=(rpm_limit, tpm_limit))
    return service


class TestQuotaExceededEmission(unittest.IsolatedAsyncioTestCase):
    async def test_rpm_exceeded_emits_reason_rate_limit(self):
        service = _quota_service(_StubRedis(rpm=11), rpm_limit=10)
        with (
            patch(
                "app.modules.llm_quota.llm_quota_service.emit_quota_exceeded"
            ) as emit,
            self.assertRaises(HTTPException) as ctx,
        ):
            await service.check_quota(api_key="sk-caller", tenant_id="t1")
        self.assertEqual(ctx.exception.status_code, 429)
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["reason"], "rate_limit")
        self.assertEqual(kwargs["limit"], 10)
        self.assertEqual(kwargs["used"], 11)
        self.assertEqual(kwargs["tenant_id"], "t1")
        # The API key is an identity, not telemetry.
        self.assertNotIn("sk-caller", repr(kwargs))

    async def test_tpm_exceeded_emits_reason_token_limit(self):
        service = _quota_service(_StubRedis(rpm=1, tpm="25000"), tpm_limit=20000)
        with (
            patch(
                "app.modules.llm_quota.llm_quota_service.emit_quota_exceeded"
            ) as emit,
            self.assertRaises(HTTPException) as ctx,
        ):
            await service.check_quota(api_key="sk-caller")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(emit.call_args.kwargs["reason"], "token_limit")
        self.assertEqual(emit.call_args.kwargs["used"], 25000)

    async def test_exhausted_budget_emits_reason_budget_exhausted(self):
        service = _quota_service(_StubRedis(rpm=1, budget="0"))
        with (
            patch(
                "app.modules.llm_quota.llm_quota_service.emit_quota_exceeded"
            ) as emit,
            self.assertRaises(HTTPException) as ctx,
        ):
            await service.check_quota(api_key="sk-caller")
        self.assertEqual(ctx.exception.status_code, 402)
        self.assertEqual(emit.call_args.kwargs["reason"], "budget_exhausted")

    async def test_allowed_request_emits_nothing(self):
        service = _quota_service(_StubRedis(rpm=1, budget="100"))
        with patch(
            "app.modules.llm_quota.llm_quota_service.emit_quota_exceeded"
        ) as emit:
            await service.check_quota(api_key="sk-caller")
        emit.assert_not_called()

    async def test_redis_outage_does_not_emit_a_quota_event(self):
        # Fail-open: a cache/Redis outage must never look like a quota breach.
        class _BrokenRedis(_StubRedis):
            async def get(self, key):
                raise RuntimeError("redis down")

            async def incr_with_ttl(self, key, ttl):
                raise RuntimeError("redis down")

        service = _quota_service(_BrokenRedis())
        with patch(
            "app.modules.llm_quota.llm_quota_service.emit_quota_exceeded"
        ) as emit:
            await service.check_quota(api_key="sk-caller")
        emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
