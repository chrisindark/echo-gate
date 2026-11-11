"""Unit tests for the shadow-cache service.

Runs on SQLite in-memory — no Postgres required. The integration suite in
``tests/integration/test_shadow_cache.py`` covers the same readiness logic
end-to-end but is skipped without a reachable database; these tests keep the
Phase 1.5 surface covered in every environment.
"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import config
from app.core.database import Base
from app.modules.gateway_requests.gateway_requests_model import (  # noqa: F401
    GatewayRequestLog,
)
from app.modules.shadow_cache.shadow_cache_model import ShadowCacheEvaluation
from app.modules.shadow_cache.shadow_cache_schema import (
    ReadinessStatus,
    ShadowCacheEvaluationCreate,
    ShadowMatchType,
)
from app.modules.shadow_cache.shadow_cache_service import ShadowCacheService

# Tracked for tearDown restore.
_THRESHOLD_ATTRS = (
    "SHADOW_READINESS_MIN_EVALUATIONS",
    "SHADOW_READINESS_MIN_HIT_RATE",
    "SHADOW_READINESS_MIN_EQUIVALENCE_RATE",
)


class _ShadowServiceTestCase(unittest.TestCase):
    """Shared SQLite harness, mirroring the integration test setup."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = ShadowCacheService(
            session_factory=self.Session, session_factory_read=self.Session
        )
        self._saved_thresholds = tuple(
            getattr(config, attr) for attr in _THRESHOLD_ATTRS
        )

    def tearDown(self):
        for attr, value in zip(_THRESHOLD_ATTRS, self._saved_thresholds, strict=True):
            setattr(config, attr, value)
        self.Session.close_all()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _add(
        self,
        *,
        match_type=ShadowMatchType.MISS,
        equivalent=None,
        intent="general_query",
        model="gpt-4o-mini",
        provider="openai",
        tenant_id="tenant-a",
        saved_ms=120,
        age_seconds=0,
    ):
        row = self.service.log_evaluation(
            ShadowCacheEvaluationCreate(
                tenant_id=tenant_id,
                exact_hash="h",
                model=model,
                provider=provider,
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

    def _window(self):
        return (
            datetime.now(UTC) - timedelta(hours=1),
            datetime.now(UTC) + timedelta(minutes=1),
        )


class TestEvaluateReadinessUnit(unittest.TestCase):
    """Direct unit tests for the pure readiness gate — no DB involved."""

    def setUp(self):
        self._saved = tuple(getattr(config, attr) for attr in _THRESHOLD_ATTRS)
        config.SHADOW_READINESS_MIN_EVALUATIONS = 10
        config.SHADOW_READINESS_MIN_HIT_RATE = 0.05
        config.SHADOW_READINESS_MIN_EQUIVALENCE_RATE = 0.98

    def tearDown(self):
        for attr, value in zip(_THRESHOLD_ATTRS, self._saved, strict=True):
            setattr(config, attr, value)

    def test_zero_evaluations_is_not_ready_with_reason(self):
        readiness = ShadowCacheService._evaluate_readiness(0, 0.0, 0, None)
        self.assertEqual(readiness.status, ReadinessStatus.NOT_READY)
        self.assertEqual(
            readiness.reasons,
            ["No shadow evaluations recorded in this window."],
        )

    def test_below_min_evaluations_is_warming_up(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=5,
            hit_rate=0.20,
            compared=2,
            equivalence_rate=1.0,
        )
        self.assertEqual(readiness.status, ReadinessStatus.WARMING_UP)
        self.assertTrue(any("required evaluations" in r for r in readiness.reasons))

    def test_no_compared_yet_is_warming_up_even_above_floor(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=500,
            hit_rate=0.20,
            compared=0,
            equivalence_rate=None,
        )
        self.assertEqual(readiness.status, ReadinessStatus.WARMING_UP)
        self.assertTrue(
            any("No shadow match has been compared" in r for r in readiness.reasons)
        )

    def test_low_hit_rate_is_not_ready_when_compared(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=500,
            hit_rate=0.01,
            compared=20,
            equivalence_rate=1.0,
        )
        self.assertEqual(readiness.status, ReadinessStatus.NOT_READY)
        self.assertTrue(any("hit rate" in r.lower() for r in readiness.reasons))

    def test_equivalence_below_floor_is_not_ready(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=500,
            hit_rate=0.20,
            compared=20,
            equivalence_rate=0.90,
        )
        self.assertEqual(readiness.status, ReadinessStatus.NOT_READY)
        self.assertTrue(any("equivalence" in r.lower() for r in readiness.reasons))

    def test_all_gates_pass_is_ready_with_no_reasons(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=500,
            hit_rate=0.10,
            compared=20,
            equivalence_rate=0.99,
        )
        self.assertEqual(readiness.status, ReadinessStatus.READY)
        self.assertEqual(readiness.reasons, [])

    def test_boundary_values_pass(self):
        """Exactly meeting each floor with enough volume is READY."""
        config.SHADOW_READINESS_MIN_EVALUATIONS = 100
        config.SHADOW_READINESS_MIN_HIT_RATE = 0.05
        config.SHADOW_READINESS_MIN_EQUIVALENCE_RATE = 0.98
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=100,
            hit_rate=0.05,
            compared=1,
            equivalence_rate=0.98,
        )
        self.assertEqual(readiness.status, ReadinessStatus.READY)

    def test_below_floor_with_no_compared_yet_still_warming_up(self):
        readiness = ShadowCacheService._evaluate_readiness(
            total_evaluations=5,
            hit_rate=0.0,
            compared=0,
            equivalence_rate=None,
        )
        self.assertEqual(readiness.status, ReadinessStatus.WARMING_UP)

    def test_thresholds_echoed_in_response(self):
        readiness = ShadowCacheService._evaluate_readiness(0, 0.0, 0, None)
        self.assertEqual(readiness.min_evaluations, 10)
        self.assertAlmostEqual(readiness.min_hit_rate, 0.05)
        self.assertAlmostEqual(readiness.min_equivalence_rate, 0.98)


class TestCoerceMatchTypeUnit(unittest.TestCase):
    def test_known_match_type_passes_through(self):
        self.assertEqual(
            ShadowCacheService._coerce_match_type("REDIS_EXACT"),
            ShadowMatchType.REDIS_EXACT,
        )

    def test_unknown_match_type_falls_back_to_miss(self):
        self.assertEqual(
            ShadowCacheService._coerce_match_type("NOT_A_LAYER"),
            ShadowMatchType.MISS,
        )
        self.assertEqual(
            ShadowCacheService._coerce_match_type(""),
            ShadowMatchType.MISS,
        )


class TestSummaryAndReadinessSqlite(_ShadowServiceTestCase):
    def test_summary_reports_rates_breakdowns_and_savings(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.QDRANT_SEMANTIC, equivalent=False)
        self._add(match_type=ShadowMatchType.MISS)

        start, end = self._window()
        summary = self.service.get_summary(
            start_time=start, end_time=end, time_range="1h"
        )

        self.assertEqual(summary.total_evaluations, 3)
        self.assertEqual(summary.would_have_hits, 2)
        self.assertAlmostEqual(summary.hit_rate, 2 / 3, places=4)
        self.assertEqual(summary.compared_evaluations, 2)
        self.assertEqual(summary.equivalent_evaluations, 1)
        self.assertEqual(summary.non_equivalent_evaluations, 1)
        self.assertAlmostEqual(summary.equivalence_rate, 0.5)
        # Saved only counts hits: 120 + 120.
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
        self.assertEqual(summary.cache_mode, config.CACHE_MODE)

    def test_empty_window_reports_not_ready(self):
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.total_evaluations, 0)
        self.assertEqual(summary.would_have_hits, 0)
        self.assertEqual(summary.hit_rate, 0.0)
        self.assertEqual(summary.equivalence_rate, None)
        self.assertEqual(summary.readiness.status, ReadinessStatus.NOT_READY)
        self.assertEqual(
            summary.readiness.reasons,
            ["No shadow evaluations recorded in this window."],
        )

    def test_warming_up_below_sample_floor(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.readiness.status, ReadinessStatus.WARMING_UP)
        self.assertTrue(
            any("required evaluations" in r for r in summary.readiness.reasons)
        )

    def test_not_ready_when_equivalence_too_low(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=False)
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.readiness.status, ReadinessStatus.NOT_READY)
        self.assertTrue(
            any("equivalence" in r.lower() for r in summary.readiness.reasons)
        )

    def test_ready_when_all_gates_pass(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.readiness.status, ReadinessStatus.READY)
        self.assertEqual(summary.readiness.reasons, [])

    def test_no_compared_yet_is_warming_up_via_summary(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        self._add(match_type=ShadowMatchType.MISS)  # never compared
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.compared_evaluations, 0)
        self.assertEqual(summary.readiness.status, ReadinessStatus.WARMING_UP)
        self.assertIn(
            "No shadow match has been compared against a fresh answer yet.",
            summary.readiness.reasons,
        )

    def test_low_hit_rate_is_not_ready_via_summary(self):
        config.SHADOW_READINESS_MIN_EVALUATIONS = 1
        config.SHADOW_READINESS_MIN_HIT_RATE = 0.99
        # 1 hit + 1 miss = 50% hit rate, below the 99% floor.
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.MISS)
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.readiness.status, ReadinessStatus.NOT_READY)
        self.assertTrue(any("hit rate" in r.lower() for r in summary.readiness.reasons))

    def test_window_excludes_older_evaluations(self):
        self._add(match_type=ShadowMatchType.REDIS_EXACT, equivalent=True)
        self._add(match_type=ShadowMatchType.MISS, age_seconds=7200)
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(summary.total_evaluations, 1)

    def test_tenant_filter_scopes_summary(self):
        self._add(
            match_type=ShadowMatchType.REDIS_EXACT, equivalent=True, tenant_id="a"
        )
        self._add(match_type=ShadowMatchType.MISS, tenant_id="b")
        start, end = self._window()
        all_summary = self.service.get_summary(start_time=start, end_time=end)
        self.assertEqual(all_summary.total_evaluations, 2)

        tenant_summary = self.service.get_summary(
            start_time=start, end_time=end, tenant_id="a"
        )
        self.assertEqual(tenant_summary.total_evaluations, 1)
        self.assertEqual(tenant_summary.would_have_hits, 1)
        self.assertAlmostEqual(tenant_summary.hit_rate, 1.0)

    def test_by_intent_and_model_breakdowns(self):
        self._add(
            match_type=ShadowMatchType.REDIS_EXACT,
            equivalent=True,
            intent="general_query",
            model="gpt-4o-mini",
        )
        self._add(
            match_type=ShadowMatchType.QDRANT_SEMANTIC,
            equivalent=True,
            intent="billing",
            model="gpt-4o-mini",
        )
        self._add(
            match_type=ShadowMatchType.MISS,
            intent=None,
            model="claude-haiku",
        )
        start, end = self._window()
        summary = self.service.get_summary(start_time=start, end_time=end)

        self.assertEqual(len(summary.by_intent), 2)
        intents = {b.intent for b in summary.by_intent}
        self.assertEqual(intents, {"general_query", "billing"})
        # None intent is excluded from breakdown.
        models = {b.model for b in summary.by_model}
        self.assertEqual(models, {"gpt-4o-mini", "claude-haiku"})


class TestGetEvaluationsSqlite(_ShadowServiceTestCase):
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

    def test_time_range_and_model_filters(self):
        self._add(model="gpt-4o-mini")
        self._add(model="claude-haiku")

        items, total = self.service.get_evaluations(model="gpt-4o-mini")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].model, "gpt-4o-mini")

        start, end = self._window()
        items, total = self.service.get_evaluations(start_time=start, end_time=end)
        self.assertEqual(total, 2)

    def test_tenant_id_match_type_and_intent_filters(self):
        self._add(
            tenant_id="tenant-1",
            match_type=ShadowMatchType.REDIS_EXACT,
            intent="billing",
        )
        self._add(
            tenant_id="tenant-2",
            match_type=ShadowMatchType.QDRANT_SEMANTIC,
            intent="coding",
        )

        items, total = self.service.get_evaluations(tenant_id="tenant-1")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].tenant_id, "tenant-1")

        items, total = self.service.get_evaluations(
            match_type=ShadowMatchType.REDIS_EXACT.value
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0].match_type, ShadowMatchType.REDIS_EXACT.value)

        items, total = self.service.get_evaluations(intent="billing")
        self.assertEqual(total, 1)
        self.assertEqual(items[0].intent, "billing")

    def test_session_injection_and_context_managers(self):
        with self.Session() as session:
            # Test direct Session injection into __init__
            service_with_session = ShadowCacheService(
                session_factory=session,
                session_factory_read=session,
            )
            self.assertIs(service_with_session._db_session, session)
            self.assertIs(service_with_session._db_session_read, session)

            with service_with_session._get_session() as s:
                self.assertIs(s, session)
            with service_with_session._get_read_session() as s:
                self.assertIs(s, session)

            # Test fallback session setups
            svc_read_only = ShadowCacheService(
                db_session=session,
                db_session_read=None,
            )
            with svc_read_only._get_read_session() as s:
                self.assertIs(s, session)

            svc_factory_read = ShadowCacheService(
                session_factory=self.Session,
                session_factory_read=None,
            )
            svc_factory_read._session_factory_read = None
            with svc_factory_read._get_read_session() as s:
                self.assertIsNotNone(s)

        from unittest.mock import MagicMock, patch

        with patch(
            "app.modules.shadow_cache.shadow_cache_service.SessionLocal"
        ) as mock_sl:
            mock_s = MagicMock()
            mock_sl.return_value.__enter__.return_value = mock_s
            svc_default = ShadowCacheService()
            svc_default._session_factory = None
            svc_default._session_factory_read = None
            with svc_default._get_session() as s:
                self.assertIs(s, mock_s)
            with svc_default._get_read_session() as s:
                self.assertIs(s, mock_s)

    def test_log_evaluation_rollback_on_error(self):
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.commit.side_effect = RuntimeError("DB commit failed")

        svc = ShadowCacheService(db_session=mock_session)
        with self.assertRaises(RuntimeError):
            svc.log_evaluation(
                ShadowCacheEvaluationCreate(
                    exact_hash="fail_hash",
                    model="gpt-4o",
                    provider="openai",
                    would_have_hit=False,
                    match_type=ShadowMatchType.MISS,
                )
            )
        mock_session.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
