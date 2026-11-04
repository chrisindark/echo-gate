"""Concurrent quota enforcement.

Rate limiting is only correct if the counter increment is atomic. These tests
fire many in-flight requests at one bucket through a Redis double whose
``incr_with_ttl`` is serialised the way Redis' INCR is, and assert the limit is
honoured exactly: no more than ``max_rpm`` requests pass, every attempt is
counted, and the advertised ``remaining`` values are distinct and in range.

A regression to read-then-write would let several callers observe the same
counter and overshoot; ``test_limit_is_exact_under_concurrency`` catches that.
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.modules.llm_quota.llm_quota_service import (
    QUOTA_WINDOW_SECONDS,
    LlmQuotaService,
)


class FakeRedis:
    """Minimal Redis double whose INCR+EXPIRE is serialised like the real thing.

    ``strings`` backs ``GET`` (budget + token counters); ``counters`` backs the
    atomic ``INCR`` used for RPM.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.strings.get(key)

    async def incr_with_ttl(self, key: str, ttl: int) -> int | None:
        if self.fail:
            raise ConnectionError("redis down")
        async with self._lock:
            self.counters[key] = self.counters.get(key, 0) + 1
            self.ttls.setdefault(key, ttl)
            return self.counters[key]

    async def remaining_ttl(self, key: str) -> int | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.ttls.get(key, QUOTA_WINDOW_SECONDS)


def _service(
    redis: FakeRedis, max_rpm: int, max_tpm: int = 1_000_000
) -> LlmQuotaService:
    service = LlmQuotaService(redis_service=redis, session_factory=object())

    async def limits(*args, **kwargs):
        return max_rpm, max_tpm

    service.get_applicable_limits = limits  # type: ignore[method-assign]
    return service


async def _outcomes(coro_fns):
    return await asyncio.gather(*coro_fns, return_exceptions=True)


async def test_limit_is_exact_under_concurrency():
    """Exactly ``max_rpm`` of many simultaneous requests pass; the rest get 429."""
    limit = 10
    in_flight = 40
    redis = FakeRedis()
    service = _service(redis, limit)

    results = await _outcomes(
        [
            service.check_quota(api_key="sk-tenant", provider="openai", model="gpt-4o")
            for _ in range(in_flight)
        ]
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, HTTPException)]

    assert len(ok) == limit, f"expected {limit} passes, got {len(ok)}"
    assert len(refused) == in_flight - limit
    assert all(r.status_code == 429 for r in refused)

    # Every attempt reached Redis, including the refused ones.
    assert sum(redis.counters.values()) == in_flight
    assert list(redis.counters.values()) == [in_flight]

    # Each passing request saw a distinct, in-range remaining count.
    assert sorted(r.remaining_requests for r in ok) == list(range(limit))


async def test_shared_bucket_is_shared_across_callers():
    """Same bucket_id means one counter, regardless of which key is presented."""
    redis = FakeRedis()
    service = _service(redis, max_rpm=3)

    results = await _outcomes(
        [
            service.check_quota(
                api_key=None,
                provider="openai",
                model="gpt-4o",
                bucket_id="anon-fingerprint-abc",
            )
            for _ in range(5)
        ]
    )

    assert len([r for r in results if not isinstance(r, BaseException)]) == 3
    assert len(redis.counters) == 1
    assert list(redis.counters)[0].startswith("rpm:anon-fingerprint-abc:")


async def test_distinct_buckets_do_not_share_the_limit():
    """One hot bucket must not exhaust another caller's allowance."""
    redis = FakeRedis()
    service = _service(redis, max_rpm=2)

    results = await _outcomes(
        [
            service.check_quota(
                api_key=None,
                provider="openai",
                model="gpt-4o",
                bucket_id=bucket,
            )
            for bucket in ("caller-a", "caller-b", "caller-c")
            for _ in range(2)
        ]
    )

    assert not [r for r in results if isinstance(r, BaseException)]
    assert len(redis.counters) == 3
    assert all(count == 2 for count in redis.counters.values())


async def test_redis_failure_fails_open():
    """A dead Redis must not throttle traffic; it degrades to allowing the call."""
    redis = FakeRedis()
    redis.fail = True
    service = _service(redis, max_rpm=1)

    results = await _outcomes(
        [
            service.check_quota(api_key="sk-tenant", provider="openai", model="gpt-4o")
            for _ in range(20)
        ]
    )

    assert not [r for r in results if isinstance(r, BaseException)]
    assert all(r.remaining_requests >= 0 for r in results)


async def test_token_limit_is_enforced_independently():
    """The TPM counter refuses with a token-specific Retry-After."""
    redis = FakeRedis()
    service = _service(redis, max_rpm=100, max_tpm=50)
    redis.strings["tpm:sk-tenant:openai:gpt-4o:any"] = "51"

    with pytest.raises(HTTPException) as excinfo:
        await service.check_quota(
            api_key="sk-tenant", provider="openai", model="gpt-4o"
        )

    assert excinfo.value.status_code == 429
    assert "Token rate limit" in excinfo.value.detail
    assert excinfo.value.headers["Retry-After"] == str(QUOTA_WINDOW_SECONDS)
    assert excinfo.value.headers["X-RateLimit-Limit-Tokens"] == "50"


async def test_exhausted_budget_is_payment_required():
    """A zero budget returns 402, not 429: the caller is out of credit, not fast."""
    redis = FakeRedis()
    redis.strings["budget:sk-tenant"] = "0"
    service = _service(redis, max_rpm=100)

    with pytest.raises(HTTPException) as excinfo:
        await service.check_quota(
            api_key="sk-tenant", provider="openai", model="gpt-4o"
        )

    assert excinfo.value.status_code == 402
