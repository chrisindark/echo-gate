"""Admin cache warmup endpoint tests.

Pins the contract of ``POST /api/v1/admin/cache/warmup``: the summary must come
back on ``BaseAPIResponse.response`` (not silently dropped), per-item failures
must be counted rather than raised, ``tenant_id`` must reach the cache key, and
fan-out must stay bounded.
"""

import asyncio
import unittest

from fastapi import HTTPException

from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm.cache_admin_controller import (
    MAX_WARMUP_ITEMS,
    WARMUP_CONCURRENCY,
    CacheWarmupItem,
    CacheWarmupRequest,
    warmup_cache,
)

IDENTITY = AdminKeyIdentity(
    key_id=1, name="test-admin", scopes=frozenset({"cache:write"})
)


def _item(tenant_id=None, prompt="hello") -> CacheWarmupItem:
    return CacheWarmupItem(
        request={
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        },
        response={
            "id": "chatcmpl-test",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "world"}}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
        tenant_id=tenant_id,
    )


class _FakeRouter:
    """Records warmup writes; ``outcomes`` decides each item's return value."""

    def __init__(self, outcomes=None, delay=0.0):
        self.calls: list[dict] = []
        self.outcomes = list(outcomes or [])
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    def _generate_cache_key(self, request) -> str:
        return f"hash:{request.tenant_id}:{request.model}"

    async def _background_save_cache(self, **kwargs) -> bool:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.calls.append(kwargs)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.calls and isinstance(self.outcomes, list):
                index = len(self.calls) - 1
                if index < len(self.outcomes):
                    outcome = self.outcomes[index]
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome
            return True
        finally:
            self.in_flight -= 1


class TestCacheWarmupEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_returned_on_response_field(self):
        router = _FakeRouter()
        result = await warmup_cache(
            CacheWarmupRequest(items=[_item(), _item()]),
            identity=IDENTITY,
            router=router,
        )

        # The bug this pins: ``BaseAPIResponse(data=...)`` is silently ignored,
        # leaving ``response`` as ``None`` and the operator with no counts.
        self.assertTrue(result.success)
        self.assertIsNotNone(result.response)
        self.assertEqual(result.response.queued, 2)
        self.assertEqual(result.response.succeeded, 2)
        self.assertEqual(result.response.failed, 0)

    async def test_failed_writes_are_counted_not_raised(self):
        router = _FakeRouter(outcomes=[True, False])
        result = await warmup_cache(
            CacheWarmupRequest(items=[_item(), _item()]),
            identity=IDENTITY,
            router=router,
        )

        self.assertEqual(result.response.succeeded, 1)
        self.assertEqual(result.response.failed, 1)

    async def test_item_exception_is_isolated(self):
        router = _FakeRouter(outcomes=[RuntimeError("qdrant down")])
        result = await warmup_cache(
            CacheWarmupRequest(items=[_item(), _item()]),
            identity=IDENTITY,
            router=router,
        )

        self.assertEqual(result.response.queued, 2)
        self.assertEqual(result.response.succeeded, 1)
        self.assertEqual(result.response.failed, 1)

    async def test_tenant_id_reaches_the_cache_write(self):
        router = _FakeRouter()
        result = await warmup_cache(
            CacheWarmupRequest(items=[_item(tenant_id="acme")]),
            identity=IDENTITY,
            router=router,
        )

        self.assertEqual(result.response.succeeded, 1)
        written = router.calls[0]["request"]
        self.assertEqual(written.tenant_id, "acme")
        self.assertEqual(router.calls[0]["exact_hash"], "hash:acme:gpt-4o-mini")

    async def test_omitted_tenant_id_writes_an_unscoped_entry(self):
        router = _FakeRouter()
        await warmup_cache(
            CacheWarmupRequest(items=[_item()]), identity=IDENTITY, router=router
        )

        self.assertIsNone(router.calls[0]["request"].tenant_id)

    async def test_force_flag_is_honoured(self):
        router = _FakeRouter()
        await warmup_cache(
            CacheWarmupRequest(items=[_item()], force=False),
            identity=IDENTITY,
            router=router,
        )

        self.assertFalse(router.calls[0]["force"])

    async def test_service_name_falls_back_to_default(self):
        item = _item()
        item.request.service_name = None
        router = _FakeRouter()
        await warmup_cache(
            CacheWarmupRequest(items=[item]), identity=IDENTITY, router=router
        )

        from app.core.config import config

        self.assertEqual(router.calls[0]["service_name"], config.DEFAULT_LLM_SERVICE)

    async def test_empty_batch_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await warmup_cache(
                CacheWarmupRequest(items=[]), identity=IDENTITY, router=_FakeRouter()
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_oversized_batch_is_rejected(self):
        items = [_item() for _ in range(MAX_WARMUP_ITEMS + 1)]
        with self.assertRaises(HTTPException) as ctx:
            await warmup_cache(
                CacheWarmupRequest(items=items), identity=IDENTITY, router=_FakeRouter()
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_concurrency_is_bounded(self):
        router = _FakeRouter(delay=0.01)
        items = [_item() for _ in range(WARMUP_CONCURRENCY * 4)]
        result = await warmup_cache(
            CacheWarmupRequest(items=items), identity=IDENTITY, router=router
        )

        self.assertEqual(result.response.succeeded, len(items))
        self.assertLessEqual(router.max_in_flight, WARMUP_CONCURRENCY)
        self.assertGreater(router.max_in_flight, 1, "writes should run in parallel")


if __name__ == "__main__":
    unittest.main()
