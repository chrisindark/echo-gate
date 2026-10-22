"""Rate-limit header contract for the public API.

Downstream callers must be able to self-throttle: every guarded response
advertises the remaining RPM/TPM budget and when the window resets, and a
throttled request additionally carries ``Retry-After``.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from starlette.testclient import TestClient

from app.core.dependencies import get_llm_quota_service, get_llm_router_service
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm_quota.llm_quota_service import LlmQuotaService, QuotaStatus

HEADER_NAMES = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
)


class _QuotaRedis:
    """Redis stub that reports a fixed usage/limit window."""

    def __init__(self, rpm=1, tpm="500", budget=None, ttl_value=30):
        self.rpm = rpm
        self.tpm = tpm
        self.budget = budget
        self.ttl_value = ttl_value

    async def get(self, key):
        if key.startswith("budget:"):
            return self.budget
        return self.tpm

    async def incr_with_ttl(self, key, ttl):
        return self.rpm

    async def remaining_ttl(self, key):
        return self.ttl_value


def _service(redis: _QuotaRedis) -> LlmQuotaService:
    return LlmQuotaService(redis_service=redis, session_factory=MagicMock())  # type: ignore[arg-type]


def _status(**overrides) -> QuotaStatus:
    values = {
        "limit_requests": 10,
        "remaining_requests": 9,
        "reset_requests_seconds": 30,
        "limit_tokens": 20000,
        "remaining_tokens": 19500,
        "reset_tokens_seconds": 42,
    }
    values.update(overrides)
    return QuotaStatus(**values)


class TestQuotaStatusComputation(unittest.IsolatedAsyncioTestCase):
    async def test_successful_check_reports_remaining_budget(self):
        status = await _service(_QuotaRedis()).check_quota(
            api_key="eg_test", provider="openai", model="gpt-4o-mini"
        )

        self.assertEqual(status.limit_requests, 10)
        self.assertEqual(status.remaining_requests, 9)  # this request counted
        self.assertEqual(status.limit_tokens, 20000)
        self.assertEqual(status.remaining_tokens, 19500)
        self.assertEqual(status.reset_requests_seconds, 30)
        self.assertEqual(status.reset_tokens_seconds, 30)

    async def test_headers_are_strings(self):
        headers = _status().to_headers()
        self.assertEqual(
            set(headers),
            {
                "X-RateLimit-Limit-Requests",
                "X-RateLimit-Remaining-Requests",
                "X-RateLimit-Reset-Requests",
                "X-RateLimit-Limit-Tokens",
                "X-RateLimit-Remaining-Tokens",
                "X-RateLimit-Reset-Tokens",
            },
        )
        self.assertTrue(all(isinstance(value, str) for value in headers.values()))

    async def test_missing_redis_ttl_falls_back_to_a_full_window(self):
        status = await _service(_QuotaRedis(ttl_value=-2)).check_quota(api_key="eg")
        self.assertEqual(status.reset_requests_seconds, 60)
        self.assertEqual(status.reset_tokens_seconds, 60)

    async def test_rpm_limit_raises_429_with_retry_after(self):
        with self.assertRaises(HTTPException) as ctx:
            await _service(_QuotaRedis(rpm=11)).check_quota(api_key="eg")

        exc = ctx.exception
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.headers["Retry-After"], "30")
        self.assertEqual(exc.headers["X-RateLimit-Limit-Requests"], "10")
        self.assertEqual(exc.headers["X-RateLimit-Remaining-Requests"], "0")

    async def test_tpm_limit_raises_429_with_token_retry_after(self):
        with self.assertRaises(HTTPException) as ctx:
            await _service(_QuotaRedis(rpm=1, tpm="25000")).check_quota(api_key="eg")

        exc = ctx.exception
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.headers["Retry-After"], "30")
        self.assertEqual(exc.headers["X-RateLimit-Remaining-Tokens"], "0")

    async def test_exhausted_budget_raises_402_with_rate_limit_headers(self):
        with self.assertRaises(HTTPException) as ctx:
            await _service(_QuotaRedis(budget="0")).check_quota(api_key="eg")

        exc = ctx.exception
        self.assertEqual(exc.status_code, 402)
        self.assertEqual(exc.headers["X-RateLimit-Limit-Requests"], "10")
        self.assertNotIn("Retry-After", exc.headers)


class TestRateLimitHeadersOnResponses(unittest.TestCase):
    """The guard must advertise budget on buffered and streamed responses."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.quota_service = MagicMock()
        app.dependency_overrides[get_llm_quota_service] = lambda: self.quota_service
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

    def test_json_response_advertises_budget(self):
        self.quota_service.check_quota = AsyncMock(return_value=_status())
        self.router.generate_completion = AsyncMock(
            return_value=ChatCompletionResponse(
                id="chatcmpl-test",
                created=1,
                model="gpt-4o-mini",
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(role="assistant", content="hi"),
                    )
                ],
            )
        )

        http_response = self._post()
        self.assertEqual(http_response.status_code, 200)
        self.assertEqual(http_response.headers["x-ratelimit-limit-requests"], "10")
        self.assertEqual(http_response.headers["x-ratelimit-remaining-requests"], "9")
        self.assertEqual(http_response.headers["x-ratelimit-reset-requests"], "30")
        self.assertEqual(http_response.headers["x-ratelimit-limit-tokens"], "20000")
        self.assertEqual(http_response.headers["x-ratelimit-remaining-tokens"], "19500")
        self.assertEqual(http_response.headers["x-ratelimit-reset-tokens"], "42")

    def test_stream_response_advertises_budget(self):
        self.quota_service.check_quota = AsyncMock(return_value=_status())

        async def stream(*args, **kwargs):
            yield ChatCompletionChunk(
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

        self.router.generate_stream = stream
        http_response = self._post(stream=True)
        self.assertEqual(http_response.status_code, 200)
        self.assertTrue(
            http_response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertEqual(http_response.headers["x-ratelimit-remaining-requests"], "9")
        self.assertEqual(http_response.headers["x-ratelimit-remaining-tokens"], "19500")

    def test_throttled_request_returns_retry_after(self):
        status = _status(remaining_requests=0)
        self.quota_service.check_quota = AsyncMock(
            side_effect=HTTPException(
                status_code=429,
                detail="Rate limit exceeded (Limit: 10 RPM)",
                headers={**status.to_headers(), "Retry-After": "30"},
            )
        )

        http_response = self._post()
        self.assertEqual(http_response.status_code, 429)
        self.assertEqual(http_response.headers["retry-after"], "30")
        self.assertEqual(http_response.headers["x-ratelimit-remaining-requests"], "0")
        for name in HEADER_NAMES:
            self.assertIn(name, http_response.headers)


if __name__ == "__main__":
    unittest.main()
