"""Value-aware cache retention tests (Phase 3, step 2).

Covers the scoring curves (TTL stretching, retention value), the retention
metadata written to Qdrant, hit counting, and the eviction sweep: cheap expired
entries go, expensive expired entries survive until the grace period lapses,
and capacity pressure evicts the lowest-value points first.

Qdrant is replaced by an in-memory fake that understands the filter shapes the
service builds, so the eviction tests assert on surviving points rather than on
the filter objects passed to the client.
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from qdrant_client.http import models

from app.core.config import config
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.intent_classifier.intent_schema import IntentEnum
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.qdrant.llm_cache_collection_service import LlmCacheCollectionService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.qdrant.retention_policy import (
    calculate_ttl_seconds,
    compute_retention_value,
    estimate_provider_cost,
    retention_multiplier,
)

DAY = 24 * 60 * 60
MAX_TTL = 30 * DAY


class FakeQdrantClientService:
    """In-memory stand-in that applies the filters the cache service builds."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.deleted_ids: list[str] = []

    # Collection lifecycle (construction path).
    def collection_exists(self, collection_name: str) -> bool:
        return False

    def create_collection(self, **kwargs) -> bool:
        return True

    def upsert(self, collection_name, points, wait=False):
        for point in points:
            self.points[str(point.id)] = dict(point.payload)

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        return [
            SimpleNamespace(id=point_id, payload=self.points[point_id])
            for point_id in ids
            if point_id in self.points
        ]

    def set_payload(self, collection_name, payload, points, **kwargs):
        for point_id in points:
            self.points[point_id].update(payload)

    def count(self, collection_name, count_filter=None, exact=True):
        return sum(
            1
            for payload in self.points.values()
            if self._matches(payload, count_filter)
        )

    def delete(self, collection_name, points_selector, wait=False):
        if isinstance(points_selector, models.PointIdsList):
            ids = [str(point_id) for point_id in points_selector.points]
        else:
            ids = [
                point_id
                for point_id, payload in self.points.items()
                if self._matches(payload, points_selector)
            ]
        for point_id in ids:
            self.points.pop(point_id, None)
            self.deleted_ids.append(point_id)

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=10,
        with_payload=True,
        with_vectors=False,
        **kwargs,
    ):
        items = list(self.points.items())
        order_by = kwargs.get("order_by")
        if order_by is not None:
            ascending = order_by.direction == models.Direction.ASC
            items.sort(
                key=lambda item: item[1].get(order_by.key) or 0.0,
                reverse=not ascending,
            )
        return [
            SimpleNamespace(id=point_id, payload=payload)
            for point_id, payload in items[:limit]
        ], None

    @classmethod
    def _matches(cls, payload, points_filter):
        if points_filter is None:
            return True
        for condition in points_filter.must or []:
            if not cls._condition_matches(payload, condition):
                return False
        if points_filter.should and not any(
            cls._condition_matches(payload, condition)
            for condition in points_filter.should
        ):
            return False
        return True

    @staticmethod
    def _condition_matches(payload, condition):
        if isinstance(condition, models.IsEmptyCondition):
            return payload.get(condition.is_empty.key) is None
        if not isinstance(condition, models.FieldCondition):
            return True

        value = payload.get(condition.key)
        if condition.range is not None:
            if value is None:
                return False
            rng = condition.range
            if rng.lt is not None and not value < rng.lt:
                return False
            if rng.gte is not None and not value >= rng.gte:
                return False
            return True
        if condition.match is not None:
            return str(value) == str(condition.match.value)
        return True


class RetentionCurveTests(unittest.TestCase):
    def test_free_fast_entry_keeps_its_base_ttl(self):
        self.assertEqual(
            calculate_ttl_seconds(
                DAY, provider_cost=0.0, latency_ms=0, max_ttl_seconds=MAX_TTL
            ),
            DAY,
        )
        self.assertEqual(retention_multiplier(0.0, 0.0), 1.0)

    def test_expensive_slow_entry_gets_a_longer_ttl(self):
        cheap = calculate_ttl_seconds(
            3600, provider_cost=0.00006, latency_ms=300, max_ttl_seconds=MAX_TTL
        )
        expensive = calculate_ttl_seconds(
            3600, provider_cost=0.0285, latency_ms=8000, max_ttl_seconds=MAX_TTL
        )
        self.assertGreater(expensive, cheap)
        self.assertGreater(expensive, 3600)

    def test_ttl_never_shortens_the_base_and_is_capped(self):
        for cost, latency in ((0.0, 0), (0.001, 50), (0.05, 30_000)):
            self.assertGreaterEqual(
                calculate_ttl_seconds(
                    DAY, provider_cost=cost, latency_ms=latency, max_ttl_seconds=MAX_TTL
                ),
                DAY,
            )
        self.assertEqual(
            calculate_ttl_seconds(
                DAY, provider_cost=1.0, latency_ms=60_000, max_ttl_seconds=MAX_TTL
            ),
            MAX_TTL,
        )

    def test_provider_cost_uses_the_pricing_table(self):
        cost = estimate_provider_cost(
            "claude",
            "claude-3-7-sonnet-20250219",
            prompt_tokens=1000,
            completion_tokens=1000,
        )
        self.assertAlmostEqual(cost, 0.018, places=6)
        self.assertEqual(estimate_provider_cost("ollama", "llama3", 1000, 1000), 0.0)

    def test_retention_value_ranks_expensive_and_frequent_entries_higher(self):
        cheap = compute_retention_value(
            provider_cost=0.00006, latency_ms=300, token_count=300
        )
        expensive = compute_retention_value(
            provider_cost=0.0285, latency_ms=8000, token_count=3500
        )
        hit = compute_retention_value(
            provider_cost=0.0285, latency_ms=8000, token_count=3500, hit_count=25
        )
        self.assertGreater(expensive, cheap)
        self.assertGreater(hit, expensive)

    def test_missing_metadata_scores_as_cheap(self):
        self.assertEqual(
            compute_retention_value(None, None, None),
            compute_retention_value(0.0, 0.0, 0),
        )


class RetentionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQdrantClientService()
        self.service = LlmCacheCollectionService(client_service=self.fake)

    def test_upsert_persists_retention_metadata(self):
        point_id = self.service.upsert(
            prompt="How do I rotate a key?",
            response={"choices": []},
            provider_cost=0.0285,
            latency_ms=4200,
            token_count=1500,
            retention_value=6.25,
            expires_at=int(time.time()) + DAY,
        )
        self.assertIsNotNone(point_id)
        payload = self.fake.points[point_id]
        self.assertAlmostEqual(payload["provider_cost"], 0.0285)
        self.assertEqual(payload["latency_ms"], 4200)
        self.assertEqual(payload["token_count"], 1500)
        self.assertAlmostEqual(payload["retention_value"], 6.25)
        self.assertEqual(payload["hit_count"], 0)

    def test_record_hit_increments_count_and_reweights_the_entry(self):
        point_id = self.service.upsert(
            prompt="expensive answer",
            response={"choices": []},
            provider_cost=0.0285,
            latency_ms=8000,
            token_count=3500,
            retention_value=compute_retention_value(0.0285, 8000, 3500),
        )
        initial_value = self.fake.points[point_id]["retention_value"]

        self.assertTrue(self.service.record_hit(point_id))
        payload = self.fake.points[point_id]
        self.assertEqual(payload["hit_count"], 1)
        self.assertGreater(payload["retention_value"], initial_value)

    def test_record_hit_on_unknown_point_is_a_noop(self):
        self.assertFalse(self.service.record_hit("does-not-exist"))


class _StubEmbeddings:
    model_name = "fake-embedding-model"

    async def get_embedding_async(self, text):
        return [0.1, 0.2, 0.3]

    def get_sparse_embedding(self, text):
        return {"indices": [1], "values": [1.0]}


class _StubIntents:
    def calculate_ttl(self, intent, time_sensitivity):
        return 3600


class _StubEntities:
    def get_tags_and_max_scope(self, text):
        return [], CacheScope.GLOBAL


class _CaptureCache:
    def __init__(self):
        self.upserts: list[dict] = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return "point-new"


def _router_for_cache_writes(cache) -> LlmRouterService:
    return LlmRouterService(
        llm_provider_service=MagicMock(),
        embedding_service=_StubEmbeddings(),
        llm_cache_service=cache,
        redis_service=MagicMock(),
        reranker_service=MagicMock(),
        intent_classifier_service=_StubIntents(),
        entity_extractor_service=_StubEntities(),
        llm_usage_service=MagicMock(),
    )


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Explain prefix caching")],
        model="gpt-4o-mini",
        service_name="openai",
    )


def _response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=0,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content="A short explanation."),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000),
    )


class CacheWriteRetentionTests(unittest.IsolatedAsyncioTestCase):
    """The router's cache write path is what actually stamps the metadata."""

    async def _save(self, response, retention_enabled=True):
        cache = _CaptureCache()
        router = _router_for_cache_writes(cache)
        before = int(time.time())
        with patch.object(config, "CACHE_RETENTION_ENABLED", retention_enabled):
            _point_id, ttl = await router.save_to_qdrant_cache(
                request=_request(),
                response=response,
                intent=IntentEnum.GENERAL_QUERY,
                service_name="openai",
                model="gpt-4o-mini",
                save_to_qdrant=True,
            )
        return cache.upserts[0], ttl, before

    async def test_slow_expensive_write_gets_a_stretched_ttl_and_metadata(self):
        response = _response()
        response.cache_info = {"latency_breakdown": {"provider_ms": 8000.0}}

        upsert, ttl, before = await self._save(response)

        self.assertGreater(ttl, 3600)
        self.assertEqual(upsert["latency_ms"], 8000)
        self.assertEqual(upsert["token_count"], 2000)
        self.assertAlmostEqual(upsert["provider_cost"], 0.00075, places=8)
        self.assertGreater(upsert["retention_value"], 5.0)
        self.assertLessEqual(
            upsert["expires_at"] - before, config.CACHE_RETENTION_MAX_TTL_SECONDS
        )

    async def test_disabled_retention_keeps_the_intent_ttl(self):
        response = _response()
        response.cache_info = {"latency_breakdown": {"provider_ms": 8000.0}}

        upsert, ttl, before = await self._save(response, retention_enabled=False)

        self.assertEqual(ttl, 3600)
        self.assertEqual(upsert["expires_at"] - before, 3600)


class ValueWeightedEvictionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQdrantClientService()
        self.service = LlmCacheCollectionService(client_service=self.fake)
        self.now = int(time.time())

    def _add_point(self, retention_value, expires_at, **kwargs):
        return self.service.upsert(
            prompt=f"prompt-{retention_value}-{expires_at}",
            response={"choices": []},
            provider_cost=kwargs.get("provider_cost", 0.0),
            latency_ms=kwargs.get("latency_ms", 0),
            token_count=kwargs.get("token_count", 0),
            retention_value=retention_value,
            expires_at=expires_at,
        )

    def _run_sweep(self, **overrides):
        values = {
            "CACHE_RETENTION_ENABLED": True,
            "CACHE_RETENTION_PROTECT_THRESHOLD": 5.0,
            "CACHE_RETENTION_GRACE_PERIOD_SECONDS": 3600,
            "CACHE_RETENTION_MAX_POINTS": 0,
        }
        values.update(overrides)
        patches = [
            patch.object(config, key, value, create=True)
            for key, value in values.items()
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.service.delete_expired()

    def test_cheap_expired_entry_is_evicted_while_a_valuable_one_is_kept(self):
        cheap = self._add_point(1.4, expires_at=self.now - 60)
        expensive = self._add_point(12.0, expires_at=self.now - 60)

        self._run_sweep()

        self.assertNotIn(cheap, self.fake.points)
        self.assertIn(expensive, self.fake.points)

    def test_protected_entry_is_evicted_once_the_grace_period_lapses(self):
        protected = self._add_point(12.0, expires_at=self.now - 3600 - 60)

        self._run_sweep()

        self.assertNotIn(protected, self.fake.points)

    def test_entry_without_retention_metadata_is_treated_as_cheap(self):
        stale = self.service.upsert(
            prompt="legacy entry",
            response={"choices": []},
            expires_at=self.now - 60,
        )

        self._run_sweep()

        self.assertNotIn(stale, self.fake.points)

    def test_retention_disabled_restores_the_flat_ttl_sweep(self):
        expired = self._add_point(12.0, expires_at=self.now - 60)
        live = self._add_point(12.0, expires_at=self.now + 3600)

        self._run_sweep(CACHE_RETENTION_ENABLED=False)

        self.assertNotIn(expired, self.fake.points)
        self.assertIn(live, self.fake.points)

    def test_capacity_pressure_evicts_lowest_value_points_first(self):
        low = self._add_point(1.0, expires_at=self.now + DAY)
        mid = self._add_point(5.0, expires_at=self.now + DAY)
        high = self._add_point(9.0, expires_at=self.now + DAY)

        self._run_sweep(CACHE_RETENTION_MAX_POINTS=2)

        self.assertNotIn(low, self.fake.points)
        self.assertIn(mid, self.fake.points)
        self.assertIn(high, self.fake.points)
        self.assertEqual(self.service.evict_over_capacity(), 0)


if __name__ == "__main__":
    unittest.main()
