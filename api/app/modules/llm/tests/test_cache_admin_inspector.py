"""Admin cache inspector tests: search, filters, eviction, and bulk eviction.

Pins the contract of ``POST /api/v1/admin/cache/search`` (semantic and exact
modes), ``DELETE /api/v1/admin/cache/entry/{point_id}`` and
``POST /api/v1/admin/cache/evict``: hits are flattened from the Qdrant payload
plus the latest audit verdict, the serving-path embedding/vector name is reused
for semantic lookups only, filters are forwarded, and eviction covers both cache
layers.
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.llm.cache_admin_controller import (
    CacheBulkEvictRequest,
    CacheSearchRequest,
    bulk_evict_cache,
    evict_cache_entry,
    search_cache,
)

IDENTITY = AdminKeyIdentity(
    key_id=7, name="test-admin", scopes=frozenset({"cache:read", "cache:write"})
)

AUDITED_AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

MATCH = {
    "id": "point-1",
    "score": 0.93,
    "payload": {
        "prompt": "model:gpt-4o-mini|user: hello",
        "user_prompt": "hello",
        "model": "gpt-4o-mini",
        "service_name": "openai",
        "intent": "general_query",
        "scope": "TENANT",
        "tenant_id": "acme",
        "exact_hash": "abc123",
        "cacheable": True,
        "created_at": 1700000000,
        "expires_at": 1900000000,
        "hit_count": 4,
        "entities": ["acme", "order-42"],
        "time_sensitivity": 0.1,
        "core_operation": "reset",
        "core_subject": "password",
        "prompt_version": "prompt-v3",
        "reranker_version": "rerank-v2",
        "cache_key_version": "key-v1",
        "provider_cost": 0.0021,
        "latency_ms": 850,
        "token_count": 420,
        "retention_value": 0.75,
        "response": {"id": "chatcmpl-1"},
    },
}


class _FakeCollection:
    def __init__(self, matches=None, delete_result=True, bulk_plan=None):
        self.matches = matches if matches is not None else [MATCH]
        self.delete_result = delete_result
        self.search_calls: list[dict] = []
        self.exact_calls: list[tuple] = []
        self.bulk_calls: list[dict] = []
        self.deleted: list[str] = []
        self.bulk_plan = (
            bulk_plan
            if bulk_plan is not None
            else {
                "matched": 3,
                "deleted": 3,
                "point_ids": ["point-1", "point-2", "point-3"],
                "exact_hashes": ["hash-1", "hash-2"],
            }
        )

    def query_points_dense(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.matches

    def search_by_exact_hash(self, exact_hash, filter_payload=None, limit=20):
        self.exact_calls.append((exact_hash, filter_payload, limit))
        return self.matches

    def bulk_evict(self, **kwargs):
        self.bulk_calls.append(kwargs)
        if isinstance(self.bulk_plan, Exception):
            raise self.bulk_plan
        return self.bulk_plan

    def delete_point(self, point_id: str) -> bool:
        self.deleted.append(point_id)
        return self.delete_result


class _FakeAuditService:
    def __init__(self, audits=None, error=None):
        self.audits = audits or {}
        self.error = error
        self.requested: list[list[str]] = []

    def get_latest_audit_by_point_ids(self, point_ids):
        self.requested.append(list(point_ids))
        if self.error:
            raise self.error
        return {pid: self.audits[pid] for pid in point_ids if pid in self.audits}


class _FakeRouter:
    def __init__(
        self,
        collection=None,
        redis=None,
        vector=None,
        embed_error=None,
        cache_audit_service=None,
    ):
        self.llm_cache_service = (
            collection if collection is not None else _FakeCollection()
        )
        self.redis_service = redis
        self.cache_audit_service = cache_audit_service
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self._embed_error = embed_error
        self.embed_calls = 0

    async def _get_embeddings(self, text, get_sparse=True, prefix=""):
        self.embed_calls += 1
        if self._embed_error:
            raise self._embed_error
        return self._vector, None


class TestCacheSearch(unittest.IsolatedAsyncioTestCase):
    async def test_hits_are_flattened_from_the_payload(self):
        router = _FakeRouter()
        result = await search_cache(
            CacheSearchRequest(query="hello"), identity=IDENTITY, router=router
        )

        self.assertEqual(result.response.count, 1)
        hit = result.response.results[0]
        self.assertEqual(hit.point_id, "point-1")
        self.assertAlmostEqual(hit.score, 0.93)
        self.assertEqual(hit.user_prompt, "hello")
        self.assertEqual(hit.model, "gpt-4o-mini")
        self.assertEqual(hit.intent, "general_query")
        self.assertEqual(hit.scope, "TENANT")
        self.assertEqual(hit.tenant_id, "acme")
        self.assertEqual(hit.exact_hash, "abc123")
        self.assertTrue(hit.cacheable)
        self.assertEqual(hit.hit_count, 4)

    async def test_search_reuses_the_serving_vector_name_and_filters(self):
        collection = _FakeCollection()
        router = _FakeRouter(collection=collection)
        await search_cache(
            CacheSearchRequest(
                query="hello",
                limit=5,
                min_score=0.5,
                model="gpt-4o-mini",
                tenant_id="acme",
            ),
            identity=IDENTITY,
            router=router,
        )

        call = collection.search_calls[0]
        self.assertEqual(call["using"], "user_prompt_embedding")
        self.assertEqual(call["limit"], 5)
        self.assertEqual(call["score_threshold"], 0.5)
        self.assertEqual(
            call["filter_payload"], {"model": "gpt-4o-mini", "tenant_id": "acme"}
        )

    async def test_no_filters_sends_no_filter_payload(self):
        collection = _FakeCollection()
        await search_cache(
            CacheSearchRequest(query="hello"),
            identity=IDENTITY,
            router=_FakeRouter(collection=collection),
        )

        self.assertIsNone(collection.search_calls[0]["filter_payload"])

    async def test_missing_collection_is_503(self):
        router = _FakeRouter()
        router.llm_cache_service = None
        with self.assertRaises(HTTPException) as ctx:
            await search_cache(
                CacheSearchRequest(query="hello"), identity=IDENTITY, router=router
            )
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_inference_unavailable_is_503(self):
        router = _FakeRouter(embed_error=InferenceUnavailable("engine down"))
        with self.assertRaises(HTTPException) as ctx:
            await search_cache(
                CacheSearchRequest(query="hello"), identity=IDENTITY, router=router
            )
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_empty_vector_is_503(self):
        router = _FakeRouter(vector=[])
        with self.assertRaises(HTTPException) as ctx:
            await search_cache(
                CacheSearchRequest(query="hello"), identity=IDENTITY, router=router
            )
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_hit_carries_payload_metadata(self):
        result = await search_cache(
            CacheSearchRequest(query="hello"), identity=IDENTITY, router=_FakeRouter()
        )

        hit = result.response.results[0]
        self.assertEqual(hit.entities, ["acme", "order-42"])
        self.assertEqual(hit.service_name, "openai")
        self.assertEqual(hit.core_operation, "reset")
        self.assertEqual(hit.core_subject, "password")
        self.assertEqual(hit.prompt_version, "prompt-v3")
        self.assertEqual(hit.reranker_version, "rerank-v2")
        self.assertEqual(hit.cache_key_version, "key-v1")
        self.assertAlmostEqual(hit.provider_cost, 0.0021)
        self.assertEqual(hit.latency_ms, 850)
        self.assertEqual(hit.token_count, 420)
        self.assertAlmostEqual(hit.retention_value, 0.75)
        self.assertEqual(hit.created_at, 1700000000)

    async def test_audit_verdict_and_scores_are_attached(self):
        audit = _FakeAuditService(
            audits={
                "point-1": {
                    "audit_id": 11,
                    "rerank_score": 0.87,
                    "vector_similarity": 0.91,
                    "is_equivalent": False,
                    "created_at": AUDITED_AT,
                }
            }
        )
        result = await search_cache(
            CacheSearchRequest(query="hello"),
            identity=IDENTITY,
            router=_FakeRouter(cache_audit_service=audit),
        )

        hit = result.response.results[0]
        self.assertEqual(audit.requested, [["point-1"]])
        self.assertAlmostEqual(hit.rerank_score, 0.87)
        self.assertAlmostEqual(hit.vector_similarity, 0.91)
        self.assertFalse(hit.is_equivalent)
        self.assertEqual(hit.last_audited_at, AUDITED_AT)

    async def test_audit_lookup_failure_still_returns_hits(self):
        audit = _FakeAuditService(error=RuntimeError("db down"))
        result = await search_cache(
            CacheSearchRequest(query="hello"),
            identity=IDENTITY,
            router=_FakeRouter(cache_audit_service=audit),
        )

        self.assertEqual(result.response.count, 1)
        self.assertIsNone(result.response.results[0].rerank_score)


class TestCacheExactSearch(unittest.IsolatedAsyncioTestCase):
    async def test_exact_mode_skips_the_inference_engine(self):
        collection = _FakeCollection()
        router = _FakeRouter(collection=collection)
        result = await search_cache(
            CacheSearchRequest(mode="exact", exact_hash="abc123", limit=5),
            identity=IDENTITY,
            router=router,
        )

        self.assertEqual(router.embed_calls, 0)
        self.assertEqual(collection.search_calls, [])
        self.assertEqual(collection.exact_calls, [("abc123", None, 5)])
        self.assertEqual(result.response.results[0].exact_hash, "abc123")

    async def test_exact_mode_forwards_filters(self):
        collection = _FakeCollection()
        await search_cache(
            CacheSearchRequest(
                mode="exact",
                exact_hash=" abc123 ",
                model="gpt-4o-mini",
                intent="general_query",
                scope="TENANT",
                tenant_id="acme",
            ),
            identity=IDENTITY,
            router=_FakeRouter(collection=collection),
        )

        self.assertEqual(
            collection.exact_calls[0],
            (
                "abc123",
                {
                    "model": "gpt-4o-mini",
                    "intent": "general_query",
                    "scope": "TENANT",
                    "tenant_id": "acme",
                },
                20,
            ),
        )

    async def test_exact_mode_qdrant_failure_is_503(self):
        class _BrokenCollection(_FakeCollection):
            def search_by_exact_hash(self, *args, **kwargs):
                raise RuntimeError("qdrant down")

        with self.assertRaises(HTTPException) as ctx:
            await search_cache(
                CacheSearchRequest(mode="exact", exact_hash="abc123"),
                identity=IDENTITY,
                router=_FakeRouter(collection=_BrokenCollection()),
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_exact_mode_requires_a_hash(self):
        with self.assertRaises(ValidationError):
            CacheSearchRequest(mode="exact")

    def test_semantic_mode_requires_a_query(self):
        with self.assertRaises(ValidationError):
            CacheSearchRequest(mode="semantic", exact_hash="abc123")
        with self.assertRaises(ValidationError):
            CacheSearchRequest(mode="semantic", query="   ")


class TestCacheEviction(unittest.IsolatedAsyncioTestCase):
    async def test_evicts_qdrant_and_redis_when_hash_known(self):
        collection = _FakeCollection()
        redis = AsyncMock()
        result = await evict_cache_entry(
            point_id="point-1",
            exact_hash="abc123",
            identity=IDENTITY,
            router=_FakeRouter(collection=collection, redis=redis),
        )

        self.assertEqual(collection.deleted, ["point-1"])
        redis.delete.assert_awaited_once_with("abc123")
        self.assertTrue(result.response.qdrant_deleted)
        self.assertTrue(result.response.redis_deleted)

    async def test_evicts_only_qdrant_without_a_hash(self):
        redis = AsyncMock()
        result = await evict_cache_entry(
            point_id="point-1",
            exact_hash=None,
            identity=IDENTITY,
            router=_FakeRouter(redis=redis),
        )

        redis.delete.assert_not_awaited()
        self.assertIsNone(result.response.redis_deleted)

    async def test_redis_failure_is_reported_not_raised(self):
        redis = AsyncMock()
        redis.delete.side_effect = RuntimeError("redis down")
        result = await evict_cache_entry(
            point_id="point-1",
            exact_hash="abc123",
            identity=IDENTITY,
            router=_FakeRouter(redis=redis),
        )

        self.assertTrue(result.response.qdrant_deleted)
        self.assertFalse(result.response.redis_deleted)

    async def test_qdrant_failure_is_500(self):
        collection = _FakeCollection(delete_result=False)
        with self.assertRaises(HTTPException) as ctx:
            await evict_cache_entry(
                point_id="point-1",
                exact_hash=None,
                identity=IDENTITY,
                router=_FakeRouter(collection=collection),
            )
        self.assertEqual(ctx.exception.status_code, 500)

    async def test_missing_collection_is_503(self):
        router = _FakeRouter()
        router.llm_cache_service = None
        with self.assertRaises(HTTPException) as ctx:
            await evict_cache_entry(
                point_id="point-1",
                exact_hash=None,
                identity=IDENTITY,
                router=router,
            )
        self.assertEqual(ctx.exception.status_code, 503)


class TestCacheBulkEviction(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_reports_matches_without_deleting(self):
        collection = _FakeCollection(
            bulk_plan={
                "matched": 12,
                "deleted": 0,
                "point_ids": [],
                "exact_hashes": [],
            }
        )
        redis = AsyncMock()
        result = await bulk_evict_cache(
            CacheBulkEvictRequest(intent="general_query", dry_run=True),
            identity=IDENTITY,
            router=_FakeRouter(collection=collection, redis=redis),
        )

        self.assertTrue(result.response.dry_run)
        self.assertEqual(result.response.matched, 12)
        self.assertEqual(result.response.deleted, 0)
        self.assertEqual(result.response.redis_deleted, 0)
        self.assertEqual(result.response.filters, {"intent": "general_query"})
        redis.delete.assert_not_awaited()

    async def test_applied_sweep_forwards_filters_and_purges_redis(self):
        collection = _FakeCollection()
        redis = AsyncMock()
        result = await bulk_evict_cache(
            CacheBulkEvictRequest(
                model="gpt-4o-mini",
                older_than_days=30,
                expired_only=True,
                dry_run=False,
                limit=500,
            ),
            identity=IDENTITY,
            router=_FakeRouter(collection=collection, redis=redis),
        )

        self.assertEqual(
            collection.bulk_calls,
            [
                {
                    "intent": None,
                    "model": "gpt-4o-mini",
                    "scope": None,
                    "tenant_id": None,
                    "older_than_days": 30.0,
                    "expired_only": True,
                    "limit": 500,
                    "dry_run": False,
                }
            ],
        )
        self.assertEqual(redis.delete.await_count, 2)
        self.assertEqual(result.response.deleted, 3)
        self.assertEqual(result.response.redis_deleted, 2)

    async def test_redis_failures_are_counted_not_raised(self):
        redis = AsyncMock()
        redis.delete.side_effect = [None, RuntimeError("redis down")]
        result = await bulk_evict_cache(
            CacheBulkEvictRequest(model="gpt-4o-mini", dry_run=False),
            identity=IDENTITY,
            router=_FakeRouter(redis=redis),
        )

        self.assertEqual(result.response.redis_deleted, 1)

    async def test_purge_redis_can_be_disabled(self):
        redis = AsyncMock()
        result = await bulk_evict_cache(
            CacheBulkEvictRequest(
                model="gpt-4o-mini", dry_run=False, purge_redis=False
            ),
            identity=IDENTITY,
            router=_FakeRouter(redis=redis),
        )

        redis.delete.assert_not_awaited()
        self.assertEqual(result.response.redis_deleted, 0)

    async def test_limit_rejection_is_400(self):
        collection = _FakeCollection(
            bulk_plan=ValueError("filter matches 5000 entries, above the 1000 limit")
        )
        with self.assertRaises(HTTPException) as ctx:
            await bulk_evict_cache(
                CacheBulkEvictRequest(model="gpt-4o-mini", dry_run=False),
                identity=IDENTITY,
                router=_FakeRouter(collection=collection),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_qdrant_failure_is_503(self):
        collection = _FakeCollection(bulk_plan=RuntimeError("qdrant down"))
        with self.assertRaises(HTTPException) as ctx:
            await bulk_evict_cache(
                CacheBulkEvictRequest(model="gpt-4o-mini"),
                identity=IDENTITY,
                router=_FakeRouter(collection=collection),
            )
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_missing_collection_is_503(self):
        router = _FakeRouter()
        router.llm_cache_service = None
        with self.assertRaises(HTTPException) as ctx:
            await bulk_evict_cache(
                CacheBulkEvictRequest(model="gpt-4o-mini"),
                identity=IDENTITY,
                router=router,
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_an_empty_filter_is_rejected(self):
        with self.assertRaises(ValidationError):
            CacheBulkEvictRequest()
        with self.assertRaises(ValidationError):
            CacheBulkEvictRequest(expired_only=False, dry_run=True)


if __name__ == "__main__":
    unittest.main()
