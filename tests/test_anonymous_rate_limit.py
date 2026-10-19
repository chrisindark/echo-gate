"""Anonymous rate-limit bucket isolation.

``POST /api/v1/chat/completions`` intentionally permits keyless callers, but
they must no longer share one Redis counter: historically every anonymous
request landed in ``rpm:None:<provider>:<model>:anonymous``, so a single
keyless caller could exhaust the default 10 RPM for all anonymous traffic.

Contract pinned here:

* The guard derives a stable per-caller ``bucket_id`` from the client
  address (preferring the first ``X-Forwarded-For`` hop) plus an optional
  ``X-Session-ID``, hashed before it is used as a Redis key suffix.
* Distinct anonymous callers get distinct buckets; the same caller gets the
  same bucket across requests.
* A keyed caller still buckets on its API key.
* ``LlmQuotaService.check_quota`` uses ``bucket_id`` for the RPM/TPM counters
  while ``api_key`` stays the identity of the paid ``budget:`` counter.

Tests stub the service/Redis layer, so they never reach Postgres or Redis.
"""

import asyncio
import unittest
from unittest.mock import MagicMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.core.dependencies import get_llm_quota_service
from app.core.guards import (
    LlmQuotaGuard,
    llm_quota_guard,
)
from app.modules.llm_quota.llm_quota_service import LlmQuotaService


class _StubRedisService:
    def __init__(self):
        self.rpm_keys: list[str] = []
        self.tpm_keys: list[str] = []
        self.budget_keys: list[str] = []

    async def get(self, key):
        if key.startswith("budget:"):
            self.budget_keys.append(key)
        return None

    async def incr_with_ttl(self, key, ttl):
        self.rpm_keys.append(key)
        return 1

    async def incrby_with_ttl(self, key, amount, ttl):
        self.tpm_keys.append(key)
        return amount


class _CaptureQuotaService:
    def __init__(self):
        self.calls: list[dict] = []

    async def check_quota(self, **kwargs):
        self.calls.append(kwargs)
        return True


def _run_guard(request, x_api_key=None):
    service = _CaptureQuotaService()
    guard = LlmQuotaGuard()
    asyncio.run(
        guard(
            request=request,
            x_api_key=x_api_key,
            llm_quota_service=service,  # type: ignore[arg-type]
        )
    )
    return service


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "203.0.113.7",
):
    """Build a real Starlette Request backed by a minimal ASGI scope."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat/completions",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "query_string": b"",
        "client": (client_host, 12345),
        "state": {"api_key": None, "tenant_id": None, "user_id": None},
    }
    return Request(scope)


class TestAnonymousFingerprint(unittest.TestCase):
    def test_anonymous_callers_get_distinct_buckets_per_ip(self):
        first = _run_guard(_make_request(client_host="203.0.113.7"))
        second = _run_guard(_make_request(client_host="203.0.113.9"))

        (first_call,) = first.calls
        (second_call,) = second.calls
        self.assertIsNone(first_call["api_key"])
        self.assertNotEqual(first_call["bucket_id"], second_call["bucket_id"])
        self.assertTrue(first_call["bucket_id"].startswith("anon:"))
        self.assertTrue(second_call["bucket_id"].startswith("anon:"))

    def test_anonymous_caller_is_stable_across_requests(self):
        first = _run_guard(_make_request(client_host="203.0.113.7"))
        second = _run_guard(_make_request(client_host="203.0.113.7"))

        self.assertEqual(first.calls[0]["bucket_id"], second.calls[0]["bucket_id"])

    def test_forwarded_for_hop_is_respected(self):
        behind_proxy = _run_guard(
            _make_request(
                headers={"X-Forwarded-For": "198.51.100.3, 10.0.0.1"},
                client_host="10.0.0.1",
            )
        )
        spoofed_same_client = _run_guard(
            _make_request(
                headers={"X-Forwarded-For": "198.51.100.4"},
                client_host="10.0.0.1",
            )
        )

        # Two distinct proxies/origins behind the same fronting address are
        # isolated even though the gateway sees the same socket address.
        self.assertNotEqual(
            behind_proxy.calls[0]["bucket_id"],
            spoofed_same_client.calls[0]["bucket_id"],
        )

    def test_session_id_splits_shared_nat_clients(self):
        plain = _run_guard(_make_request(client_host="203.0.113.7"))
        with_session = _run_guard(
            _make_request(
                client_host="203.0.113.7",
                headers={"X-Session-ID": "sess-42"},
            )
        )
        other_session = _run_guard(
            _make_request(
                client_host="203.0.113.7",
                headers={"X-Session-ID": "sess-43"},
            )
        )

        self.assertNotEqual(
            plain.calls[0]["bucket_id"], with_session.calls[0]["bucket_id"]
        )
        self.assertNotEqual(
            with_session.calls[0]["bucket_id"],
            other_session.calls[0]["bucket_id"],
        )

    def test_keyed_caller_buckets_on_its_api_key(self):
        service = _run_guard(
            _make_request(headers={"X-API-Key": "vk_live_key"}), x_api_key="vk_live_key"
        )

        (call,) = service.calls
        self.assertEqual(call["api_key"], "vk_live_key")
        self.assertEqual(call["bucket_id"], "vk_live_key")


class TestQuotaEndpointIntegration(unittest.TestCase):
    """End-to-end through the FastAPI dependency wiring the guard uses."""

    def setUp(self):
        self.service = _CaptureQuotaService()
        self.app = FastAPI()

        def make_endpoint():
            async def endpoint(request: Request):
                return {"ok": True}

            return endpoint

        self.app.add_api_route(
            "/chat",
            make_endpoint(),
            methods=["POST"],
            dependencies=[Depends(llm_quota_guard)],
        )
        self.app.dependency_overrides[get_llm_quota_service] = lambda: self.service
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def test_two_anonymous_callers_do_not_share_a_bucket(self):
        for _ in range(10):
            resp = self.client.post(
                "/chat",
                headers={"X-Forwarded-For": "198.51.100.10"},
                json={},
            )
            self.assertEqual(resp.status_code, 200)
        for _ in range(10):
            resp = self.client.post(
                "/chat",
                headers={"X-Forwarded-For": "198.51.100.11"},
                json={},
            )
            self.assertEqual(resp.status_code, 200)

        bucket_ids = {call["bucket_id"] for call in self.service.calls}
        self.assertEqual(len(bucket_ids), 2)

    def test_body_provider_and_model_are_forwarded(self):
        resp = self.client.post(
            "/chat",
            json={"service_name": "openai", "model": "gpt-4o-mini"},
        )
        self.assertEqual(resp.status_code, 200)
        (call,) = self.service.calls
        self.assertEqual(call["provider"], "openai")
        self.assertEqual(call["model"], "gpt-4o-mini")


class TestCheckQuotaBucketKeying(unittest.IsolatedAsyncioTestCase):
    async def test_buckets_key_on_bucket_id(self):
        redis = _StubRedisService()
        service = LlmQuotaService(redis_service=redis, session_factory=MagicMock())

        await service.check_quota(
            api_key=None,
            provider="openai",
            model="gpt-4o-mini",
            bucket_id="anon:abc123",
        )

        self.assertEqual(redis.rpm_keys, ["rpm:anon:abc123:openai:gpt-4o-mini:any"])

    async def test_no_bucket_id_falls_back_to_api_key(self):
        redis = _StubRedisService()
        service = LlmQuotaService(redis_service=redis, session_factory=MagicMock())

        await service.check_quota(api_key="vk_live_key", provider="openai")

        self.assertEqual(redis.rpm_keys, ["rpm:vk_live_key:openai:any:any"])

    async def test_budget_still_keys_on_api_key_including_shared_anon(self):
        redis = _StubRedisService()
        service = LlmQuotaService(redis_service=redis, session_factory=MagicMock())

        await service.check_quota(api_key=None, bucket_id="anon:abc123", provider=None)

        self.assertEqual(redis.budget_keys, ["budget:None"])

    async def test_historical_shared_anonymous_bucket_is_no_longer_reachable(self):
        # Regression pin: the bucket_id the guard now derives can never be the
        # literal ``None``/``anonymous`` shared suffix of the old behavior.
        redis = _StubRedisService()
        service = LlmQuotaService(redis_service=redis, session_factory=MagicMock())

        await service.check_quota(api_key=None, bucket_id=None)
        await service.check_quota(api_key=None, bucket_id="anon:532eaabd957488040f0a")

        self.assertNotEqual(redis.rpm_keys[0].split(":")[1], "anon")
        self.assertTrue(redis.rpm_keys[0].startswith("rpm:None:"))
        self.assertEqual(redis.rpm_keys[1], "rpm:anon:532eaabd957488040f0a:any:any:any")


if __name__ == "__main__":
    unittest.main()
