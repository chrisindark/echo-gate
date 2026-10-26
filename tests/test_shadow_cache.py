"""Shadow cache pipeline tests.

Covers the ``CACHE_MODE`` gate, the out-of-band evaluator (lookup-before-write,
match classification, judge equivalence), the serving path's failure to touch
the cache in ``shadow`` mode, and the readiness aggregation.
"""

import asyncio
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import circuit_breaker_registry
from app.core.config import config
from app.core.database import Base
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
)
from app.modules.gateway_requests.gateway_requests_model import (  # noqa: F401
    GatewayRequestLog,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.shadow_cache.shadow_cache_model import ShadowCacheEvaluation
from app.modules.shadow_cache.shadow_cache_schema import (
    ReadinessStatus,
    ShadowCacheEvaluationCreate,
    ShadowMatchType,
)
from app.modules.shadow_cache.shadow_cache_service import ShadowCacheService

_CACHE_ENV = (
    "CACHE_MODE",
    "USE_REDIS_EXACT_MATCHING",
    "USE_QDRANT_EXACT_MATCHING",
    "USE_QDRANT_SEMANTIC_MATCHING",
    "SHADOW_SAMPLE_RATE",
)


def _response(content="fresh answer", model="gpt-4o-mini") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content=content))
        ],
    )


def _request(model="gpt-4o-mini", service="openai") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name=service,
        model=model,
        messages=[ChatMessage(role="user", content="how do I reset a password?")],
    )


class _EnvBackedConfigTest(unittest.TestCase):
    """Reload config from a controlled environment, then restore it."""

    def setUp(self):
        self._env_backup = {name: os.environ.get(name) for name in _CACHE_ENV}

    def tearDown(self):
        for name, value in self._env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        config.load()

    def _load_with(self, **values: str) -> None:
        for name in _CACHE_ENV:
            os.environ.pop(name, None)
        for name, value in values.items():
            os.environ[name] = value
        config.load()


class TestCacheModeResolution(_EnvBackedConfigTest):
    def test_defaults_to_disabled_when_no_flags_are_set(self):
        self._load_with()
        self.assertEqual(config.CACHE_MODE, "disabled")

    def test_legacy_flags_derive_active_mode(self):
        """Deployments that predate CACHE_MODE keep their behaviour."""
        self._load_with(USE_REDIS_EXACT_MATCHING="true")
        self.assertEqual(config.CACHE_MODE, "active")
        self.assertTrue(config.cache_layer_enabled("redis"))
        self.assertFalse(config.cache_layer_enabled("qdrant_semantic"))

    def test_explicit_shadow_mode_wins_over_layer_flags(self):
        self._load_with(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="true",
            USE_QDRANT_EXACT_MATCHING="true",
            USE_QDRANT_SEMANTIC_MATCHING="true",
        )
        self.assertEqual(config.CACHE_MODE, "shadow")
        # Nothing may serve from cache in shadow mode; the background pipeline
        # forces the layers instead.
        for layer in ("redis", "qdrant_exact", "qdrant_semantic"):
            self.assertFalse(config.cache_layer_enabled(layer))

    def test_explicit_disabled_mode_overrides_layer_flags(self):
        self._load_with(CACHE_MODE="disabled", USE_REDIS_EXACT_MATCHING="true")
        self.assertEqual(config.CACHE_MODE, "disabled")
        self.assertFalse(config.cache_layer_enabled("redis"))

    def test_unknown_mode_falls_back_to_legacy_flags(self):
        self._load_with(CACHE_MODE="observe", USE_QDRANT_EXACT_MATCHING="true")
        self.assertEqual(config.CACHE_MODE, "active")

    def test_shadow_sample_rate_is_clamped(self):
        self._load_with(CACHE_MODE="shadow", SHADOW_SAMPLE_RATE="5")
        self.assertEqual(config.SHADOW_SAMPLE_RATE, 1.0)
        self._load_with(CACHE_MODE="shadow", SHADOW_SAMPLE_RATE="-1")
        self.assertEqual(config.SHADOW_SAMPLE_RATE, 0.0)

    def test_unknown_cache_layer_raises(self):
        self._load_with(CACHE_MODE="active")
        with self.assertRaises(ValueError):
            config.cache_layer_enabled("nonexistent")


class _Provider:
    def __init__(self, content="fresh answer"):
        self.content = content
        self.calls = 0

    async def generate_openai_completion(self, request):
        self.calls += 1
        return _response(self.content)


class _Redis:
    """In-memory stand-in for ``RedisService`` (only the methods the router uses)."""

    def __init__(self, payload: str | None = None):
        self.payload = payload
        self.gets: list[str] = []
        self.sets: list[tuple] = []
        self.ops: list[str] = []

    async def get(self, key):
        self.gets.append(key)
        self.ops.append("get")
        return self.payload

    async def set(self, key, value, ttl=None):
        self.sets.append((key, value, ttl))
        self.ops.append("set")

    async def delete(self, key):
        return None


class _CacheService:
    def __init__(self, exact_payload=None, point_id=None):
        self.exact_payload = exact_payload
        self.point_id = point_id
        self.search_exact_calls = 0
        self.upserts: list[dict] = []

    def search_exact(self, exact_hash, tenant_id):
        self.search_exact_calls += 1
        return self.exact_payload, self.point_id

    def query_points_dense(self, **kwargs):
        return []

    def query_points_rrf(self, **kwargs):
        return []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return "point-new"


class _Embeddings:
    model_name = "fake-embedding-model"

    async def get_embedding_async(self, text):
        return [0.1, 0.2, 0.3]

    def get_sparse_embedding(self, text):
        return {"indices": [1], "values": [1.0]}


class _Intents:
    async def classify_intent(self, request):
        return None

    def calculate_ttl(self, intent, time_sensitivity):
        return 3600


class _Entities:
    def get_tags_and_max_scope(self, text):
        return ["TAG:password"], CacheScope.TENANT

    def get_qdrant_entity_tags(self, text):
        return ["TAG:password"]


class _Judge:
    def __init__(self, verdict=True):
        self.verdict = verdict
        self.calls: list[dict] = []

    async def evaluate_equivalence(self, query_text, cached_response, actual_response):
        self.calls.append(
            {
                "query": query_text,
                "cached": cached_response,
                "actual": actual_response,
            }
        )
        return self.verdict


class _ShadowCapture:
    def __init__(self):
        self.rows: list[ShadowCacheEvaluationCreate] = []

    def log_evaluation(self, evaluation):
        self.rows.append(evaluation)
        return evaluation


def _router(
    provider,
    *,
    redis=None,
    cache=None,
    shadow=None,
    judge=None,
) -> LlmRouterService:
    return LlmRouterService(
        llm_provider_service=provider,
        embedding_service=_Embeddings(),
        llm_cache_service=cache,
        redis_service=redis or _Redis(),
        reranker_service=MagicMock(),
        intent_classifier_service=_Intents(),
        entity_extractor_service=_Entities(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(
            log_request=MagicMock(return_value=SimpleNamespace(id=42))
        ),
        shadow_cache_service=shadow,
        judge_service=judge,
    )


class _RouterConfigTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()
        self._saved = {
            name: getattr(config, name)
            for name in (
                "CACHE_MODE",
                "USE_FALLBACK_LLM",
                "USE_REDIS_EXACT_MATCHING",
                "LLM_MAX_RETRIES",
                "SHADOW_SAMPLE_RATE",
                "SHADOW_JUDGE_ENABLED",
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
            )
        }
        config.USE_FALLBACK_LLM = "false"
        config.LLM_MAX_RETRIES = 0
        config.SHADOW_SAMPLE_RATE = 1.0
        config.SHADOW_JUDGE_ENABLED = True
        config.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 100

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()


class TestShadowEvaluation(_RouterConfigTest):
    async def test_qdrant_exact_hit_is_recorded_with_judged_equivalence(self):
        config.CACHE_MODE = "shadow"
        cached = _response("cached answer")
        cache = _CacheService(
            exact_payload={"response": cached.model_dump(exclude={"cache_info"})},
            point_id="point-old",
        )
        shadow = _ShadowCapture()
        judge = _Judge(verdict=True)
        router = _router(_Provider(), cache=cache, shadow=shadow, judge=judge)

        await router._background_shadow_evaluate(
            request=_request(),
            response=_response("fresh answer"),
            exact_hash="hash-1",
            service_name="openai",
            model="gpt-4o-mini",
        )

        self.assertEqual(len(shadow.rows), 1)
        row = shadow.rows[0]
        self.assertTrue(row.would_have_hit)
        self.assertEqual(row.match_type, ShadowMatchType.QDRANT_EXACT)
        self.assertEqual(row.point_id, "point-old")
        self.assertEqual(row.cached_response_text, "cached answer")
        self.assertEqual(row.actual_response_text, "fresh answer")
        self.assertIs(row.is_equivalent, True)
        self.assertEqual(len(judge.calls), 1)
        self.assertIn("cache_lookup_total_ms", row.latency_breakdown)
        self.assertEqual(row.entities, ["TAG:password"])

    async def test_lookup_runs_before_the_write(self):
        """Otherwise the request would 'match' the entry it just created."""
        config.CACHE_MODE = "shadow"
        cache = _CacheService(exact_payload=None, point_id=None)
        shadow = _ShadowCapture()
        router = _router(_Provider(), cache=cache, shadow=shadow)

        await router._background_shadow_evaluate(
            request=_request(),
            response=_response(),
            exact_hash="hash-2",
            service_name="openai",
            model="gpt-4o-mini",
        )

        row = shadow.rows[0]
        self.assertFalse(row.would_have_hit)
        self.assertEqual(row.match_type, ShadowMatchType.MISS)
        self.assertIsNone(row.is_equivalent)
        # The pipeline still built the corpus.
        self.assertEqual(len(cache.upserts), 1)
        self.assertEqual(
            cache.upserts[0]["exact_hash"],
            router._generate_cache_key(_request()),
        )

    async def test_identical_text_skips_the_judge(self):
        config.CACHE_MODE = "shadow"
        cached = _response("same answer")
        cache = _CacheService(
            exact_payload={"response": cached.model_dump(exclude={"cache_info"})},
            point_id="point-old",
        )
        shadow = _ShadowCapture()
        judge = _Judge(verdict=False)
        router = _router(_Provider(), cache=cache, shadow=shadow, judge=judge)

        await router._background_shadow_evaluate(
            request=_request(),
            response=_response("same answer"),
            exact_hash="hash-3",
            service_name="openai",
            model="gpt-4o-mini",
        )

        self.assertIs(shadow.rows[0].is_equivalent, True)
        self.assertEqual(judge.calls, [])

    async def test_judge_can_be_disabled_by_config(self):
        config.CACHE_MODE = "shadow"
        config.SHADOW_JUDGE_ENABLED = False
        cached = _response("cached answer")
        cache = _CacheService(
            exact_payload={"response": cached.model_dump(exclude={"cache_info"})},
            point_id="point-old",
        )
        shadow = _ShadowCapture()
        judge = _Judge(verdict=True)
        router = _router(_Provider(), cache=cache, shadow=shadow, judge=judge)

        await router._background_shadow_evaluate(
            request=_request(),
            response=_response("fresh answer"),
            exact_hash="hash-4",
            service_name="openai",
            model="gpt-4o-mini",
        )

        self.assertIsNone(shadow.rows[0].is_equivalent)
        self.assertEqual(judge.calls, [])

    async def test_redis_exact_hit_is_classified_and_links_the_trace(self):
        config.CACHE_MODE = "shadow"
        cached = _response("redis answer")
        redis = _Redis(
            json.dumps(
                {
                    "response": cached.model_dump(exclude={"cache_info"}),
                    "point_id": "point-redis",
                }
            )
        )
        shadow = _ShadowCapture()
        router = _router(
            _Provider(),
            redis=redis,
            cache=_CacheService(),
            shadow=shadow,
            judge=_Judge(True),
        )

        async def _gateway_log() -> int:
            return 7

        task = asyncio.create_task(_gateway_log())
        await router._background_shadow_evaluate(
            request=_request(),
            response=_response("fresh answer"),
            exact_hash="hash-5",
            service_name="openai",
            model="gpt-4o-mini",
            gateway_log_task=task,
        )

        row = shadow.rows[0]
        self.assertEqual(row.match_type, ShadowMatchType.REDIS_EXACT)
        self.assertEqual(row.point_id, "point-redis")
        self.assertEqual(row.request_id, 7)
        self.assertIs(row.is_equivalent, True)

    async def test_failure_in_the_pipeline_is_swallowed(self):
        config.CACHE_MODE = "shadow"
        shadow = MagicMock()
        shadow.log_evaluation.side_effect = RuntimeError("db down")
        router = _router(_Provider(), cache=_CacheService(), shadow=shadow)

        # Must not raise: a background observer may never disturb serving.
        await router._background_shadow_evaluate(
            request=_request(),
            response=_response(),
            exact_hash="hash-6",
            service_name="openai",
            model="gpt-4o-mini",
        )


class TestShadowServingPath(_RouterConfigTest):
    async def test_shadow_mode_serves_from_provider_and_defers_the_cache(self):
        config.CACHE_MODE = "shadow"
        cached = _response("cached answer")
        redis = _Redis(
            json.dumps(
                {
                    "response": cached.model_dump(exclude={"cache_info"}),
                    "point_id": "point-redis",
                }
            )
        )
        cache = _CacheService()
        provider = _Provider("fresh answer")
        shadow = _ShadowCapture()
        router = _router(
            provider, redis=redis, cache=cache, shadow=shadow, judge=_Judge(True)
        )

        response = await router.generate_completion(_request())
        await router.shutdown()

        # The client was served by the provider, not the cache.
        self.assertEqual(provider.calls, 1)
        self.assertEqual(response.choices[0].message.content, "fresh answer")
        self.assertFalse(response.cache_info["matched"])
        self.assertEqual(response.cache_info["source"], "llm")

        # Redis was consulted exactly once — by the shadow evaluator, after the
        # response — and the would-be hit was recorded.
        self.assertEqual(len(redis.gets), 1)
        self.assertEqual(len(shadow.rows), 1)
        self.assertTrue(shadow.rows[0].would_have_hit)
        self.assertEqual(shadow.rows[0].match_type, ShadowMatchType.REDIS_EXACT)
        self.assertEqual(shadow.rows[0].request_id, 42)
        # The lookup came first; the corpus write followed with the fresh answer.
        self.assertEqual(redis.ops[0], "get")
        self.assertIn("set", redis.ops)
        written = json.loads(redis.sets[0][1])
        self.assertEqual(
            written["response"]["choices"][0]["message"]["content"], "fresh answer"
        )

    async def test_active_mode_still_serves_from_cache(self):
        config.CACHE_MODE = "active"
        config.USE_REDIS_EXACT_MATCHING = "true"
        cached = _response("cached answer")
        redis = _Redis(
            json.dumps(
                {
                    "response": cached.model_dump(exclude={"cache_info"}),
                    "point_id": "point-redis",
                }
            )
        )
        provider = _Provider("fresh answer")
        shadow = _ShadowCapture()
        router = _router(provider, redis=redis, shadow=shadow)

        response = await router.generate_completion(_request())
        await router.shutdown()

        self.assertEqual(provider.calls, 0)
        self.assertEqual(response.choices[0].message.content, "cached answer")
        self.assertTrue(response.cache_info["matched"])
        self.assertEqual(
            shadow.rows, [], "active mode must not run the shadow pipeline"
        )

    async def test_disabled_mode_skips_cache_reads_but_still_calls_the_provider(self):
        config.CACHE_MODE = "disabled"
        config.USE_REDIS_EXACT_MATCHING = "true"
        redis = _Redis(json.dumps({"response": _response("cached").model_dump()}))
        provider = _Provider("fresh answer")
        shadow = _ShadowCapture()
        router = _router(provider, redis=redis, shadow=shadow)

        response = await router.generate_completion(_request())
        await router.shutdown()

        self.assertEqual(provider.calls, 1)
        self.assertEqual(response.choices[0].message.content, "fresh answer")
        self.assertEqual(redis.gets, [])
        self.assertEqual(shadow.rows, [])

    def test_should_run_respects_mode_sampling_and_mock_responses(self):
        router = _router(_Provider(), shadow=_ShadowCapture())

        config.CACHE_MODE = "active"
        self.assertFalse(router._should_run_shadow_evaluation(_response()))

        config.CACHE_MODE = "shadow"
        config.SHADOW_SAMPLE_RATE = 0.0
        self.assertFalse(router._should_run_shadow_evaluation(_response()))

        config.SHADOW_SAMPLE_RATE = 1.0
        self.assertTrue(router._should_run_shadow_evaluation(_response()))

        # A truncated stream is not the answer that would have been cached.
        overflowed = _response()
        overflowed.cache_info = {"overflowed": True}
        self.assertFalse(router._should_run_shadow_evaluation(overflowed))


class _SqliteShadowCacheTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = ShadowCacheService(
            session_factory=self.Session, session_factory_read=self.Session
        )
        self._saved_thresholds = (
            config.SHADOW_READINESS_MIN_EVALUATIONS,
            config.SHADOW_READINESS_MIN_HIT_RATE,
            config.SHADOW_READINESS_MIN_EQUIVALENCE_RATE,
        )

    def tearDown(self):
        (
            config.SHADOW_READINESS_MIN_EVALUATIONS,
            config.SHADOW_READINESS_MIN_HIT_RATE,
            config.SHADOW_READINESS_MIN_EQUIVALENCE_RATE,
        ) = self._saved_thresholds
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _add(
        self,
        *,
        match_type=ShadowMatchType.MISS,
        equivalent=None,
        intent="general_query",
        model="gpt-4o-mini",
        saved_ms=120,
        age_seconds=0,
    ):
        row = self.service.log_evaluation(
            ShadowCacheEvaluationCreate(
                tenant_id="tenant-a",
                exact_hash="h",
                model=model,
                provider="openai",
                would_have_hit=match_type != ShadowMatchType.MISS,
                match_type=match_type,
                is_equivalent=equivalent,
                intent=intent,
                latency_breakdown={"cache_lookup_total_ms": 30.0},
                provider_latency_ms=150,
                cache_lookup_latency_ms=30,
                projected_latency_saved_ms=(
                    saved_ms if match_type != ShadowMatchType.MISS else None
                ),
            )
        )
        if age_seconds:
            with self.Session() as s:
                stored = s.get(ShadowCacheEvaluation, row.id)
                stored.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
                s.commit()
        return row

    def test_summary_reports_rates_breakdowns_and_savings(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.QDRANT_SEMANTIC, equivalent=False)
        self._add(match_type=ShadowMatchType.MISS)

        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC) + timedelta(minutes=1),
            time_range="1h",
        )

        self.assertEqual(summary.total_evaluations, 3)
        self.assertEqual(summary.would_have_hits, 2)
        self.assertAlmostEqual(summary.hit_rate, 0.6667)
        self.assertEqual(summary.compared_evaluations, 2)
        self.assertEqual(summary.equivalent_evaluations, 1)
        self.assertEqual(summary.non_equivalent_evaluations, 1)
        self.assertAlmostEqual(summary.equivalence_rate, 0.5)
        self.assertEqual(summary.projected_latency_saved_ms, 240)
        self.assertEqual(
            {item.match_type for item in summary.match_type_breakdown},
            {
                ShadowMatchType.REDIS_EXACT,
                ShadowMatchType.QDRANT_SEMANTIC,
                ShadowMatchType.MISS,
            },
        )
        self.assertEqual(summary.by_model[0].would_have_hits, 2)
        self.assertEqual(summary.by_intent[0].intent, "general_query")

    def test_readiness_is_warming_up_below_the_sample_floor(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)

        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC) + timedelta(minutes=1),
        )

        self.assertEqual(summary.readiness.status, ReadinessStatus.WARMING_UP)
        self.assertTrue(
            any("required evaluations" in r for r in summary.readiness.reasons)
        )

    def test_readiness_is_not_ready_when_equivalence_is_too_low(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=False)

        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC) + timedelta(minutes=1),
        )

        self.assertEqual(summary.readiness.status, ReadinessStatus.NOT_READY)
        self.assertTrue(any("equivalence" in r for r in summary.readiness.reasons))

    def test_readiness_is_ready_when_all_gates_pass(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)

        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC) + timedelta(minutes=1),
        )

        self.assertEqual(summary.readiness.status, ReadinessStatus.READY)
        self.assertEqual(summary.readiness.reasons, [])

    def test_empty_window_reports_not_ready(self):
        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC),
        )

        self.assertEqual(summary.total_evaluations, 0)
        self.assertEqual(summary.readiness.status, ReadinessStatus.NOT_READY)

    def test_window_excludes_older_evaluations(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.MISS, age_seconds=7200)

        summary = self.service.get_summary(
            start_time=datetime.now(UTC) - timedelta(hours=1),
            end_time=datetime.now(UTC) + timedelta(minutes=1),
        )

        self.assertEqual(summary.total_evaluations, 1)

    def test_filters_and_pagination(self):
        hit = self._add(match_type=ShadowMatchType.QDRANT_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.MISS)

        items, total = self.service.get_evaluations(would_have_hit=True)
        self.assertEqual(total, 1)
        self.assertEqual(items[0].id, hit.id)

        items, total = self.service.get_evaluations(is_equivalent=False)
        self.assertEqual((items, total), ([], 0))

        items, total = self.service.get_evaluations(limit=1)
        self.assertEqual(total, 2)
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
