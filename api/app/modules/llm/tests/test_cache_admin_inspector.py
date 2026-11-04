"""Admin cache inspector tests: semantic search and single-entry eviction.

Pins the contract of ``POST /api/v1/admin/cache/search`` and
``DELETE /api/v1/admin/cache/entry/{point_id}``: hits are flattened from the
Qdrant payload, the serving-path embedding/vector name is reused, filters are
forwarded, and eviction covers both cache layers.
"""

import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.llm.cache_admin_controller import (
    CacheSearchRequest,
    evict_cache_entry,
    search_cache,
)

IDENTITY = AdminKeyIdentity(
    key_id=7, name="test-admin", scopes=frozenset({"cache:read", "cache:write"})
)

MATCH = {
    "id": "point-1",
    "score": 0.93,
    "payload": {
        "prompt": "model:gpt-4o-mini|user: hello",
        "user_prompt": "hello",
        "model": "gpt-4o-mini",
        "intent": "general_query",
        "scope": "TENANT",
        "tenant_id": "acme",
        "exact_hash": "abc123",
        "cacheable": True,
        "expires_at": 1900000000,
        "hit_count": 4,
        "response": {"id": "chatcmpl-1"},
    },
}


class _FakeCollection:
    def __init__(self, matches=None, delete_result=True):
        self.matches = matches if matches is not None else [MATCH]
        self.delete_result = delete_result
        self.search_calls: list[dict] = []
        self.deleted: list[str] = []

    def query_points_dense(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.matches

    def delete_point(self, point_id: str) -> bool:
        self.deleted.append(point_id)
        return self.delete_result


class _FakeRouter:
    def __init__(self, collection=None, redis=None, vector=None, embed_error=None):
        self.llm_cache_service = collection if collection is not None else _FakeCollection()
        self.redis_service = redis
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self._embed_error = embed_error

    async def _get_embeddings(self, text, get_sparse=True, prefix=""):
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
        self.assertEqual(call["filter_payload"], {"model": "gpt-4o-mini", "tenant_id": "acme"})

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


if __name__ == "__main__":
    unittest.main()
