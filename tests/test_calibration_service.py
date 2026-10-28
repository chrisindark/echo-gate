"""Unit tests for Conformal Risk Control intent threshold calibration.

Covers:
- compute_conformal_threshold quantile math
- IntentCalibrationService single intent and all intents calibration
- Redis override persistence, lookup, and deletion
- Dynamic threshold resolution in CacheConfidenceEvaluator and LlmRouterService
- Celery calibration task execution
"""

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import config
from app.core.database import Base
from app.modules.cache_audit.cache_audit_model import CacheEvaluation
from app.modules.cache_audit.cache_audit_schema import (
    IntentCalibrationResult,
)
from app.modules.cache_audit.calibration_service import (
    IntentCalibrationService,
    compute_conformal_threshold,
    threshold_redis_key,
)
from app.modules.cache_audit.tasks import _calibrate_intent_thresholds_async
from app.modules.llm.cache_confidence import CacheConfidenceEvaluator
from app.modules.llm.llm_router_service import LlmRouterService


class TestConformalThresholdMath(unittest.TestCase):
    def test_empty_scores_fallback_to_default(self):
        th, err_rate, err_count = compute_conformal_threshold(
            scores=[],
            is_equivalent=[],
            alpha=0.02,
            default_threshold=0.95,
        )
        self.assertEqual(th, 0.95)
        self.assertEqual(err_rate, 0.0)
        self.assertEqual(err_count, 0)

    def test_pristine_scores_zero_errors(self):
        """When all audited samples are equivalent (0 errors), threshold adapts down to min score (bounded)."""
        scores = [0.91, 0.92, 0.93, 0.95, 0.97, 0.98]
        is_eq = [True, True, True, True, True, True]

        th, err_rate, err_count = compute_conformal_threshold(
            scores=scores,
            is_equivalent=is_eq,
            alpha=0.02,
            default_threshold=0.95,
            min_bound=0.85,
        )
        # min score is 0.91; since 0.91 >= min_bound, adapts to 0.91
        self.assertEqual(th, 0.91)
        self.assertEqual(err_rate, 0.0)
        self.assertEqual(err_count, 0)

    def test_errors_at_lower_scores_calibrates_safely_above_them(self):
        """Errors at 0.90 and 0.92 should push the threshold above 0.92 to achieve <= 2% error rate."""
        # 10 samples: 2 errors at 0.90 and 0.92, 8 correct hits between 0.93 and 0.98
        scores = [0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.985, 0.99]
        is_eq = [False, False, True, True, True, True, True, True, True, True]

        # At tau <= 0.92: errors are included. At tau > 0.92: 0 errors <= 0.02
        th, err_rate, err_count = compute_conformal_threshold(
            scores=scores,
            is_equivalent=is_eq,
            alpha=0.02,
            default_threshold=0.95,
        )
        self.assertGreater(th, 0.92)
        self.assertLessEqual(th, 0.93)
        self.assertEqual(err_rate, 0.0)
        self.assertEqual(err_count, 0)

    def test_severe_error_rate_caps_at_max_bound(self):
        """If error rate is too high throughout, threshold is capped at max_bound."""
        scores = [0.90, 0.95, 0.98, 0.99]
        is_eq = [False, False, False, False]

        th, _, _ = compute_conformal_threshold(
            scores=scores,
            is_equivalent=is_eq,
            alpha=0.02,
            default_threshold=0.95,
            max_bound=0.99,
        )
        self.assertEqual(th, 0.99)


class TestIntentCalibrationService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.mock_redis = MagicMock()
        self.service = IntentCalibrationService(
            session_factory=self.Session,
            redis_service=self.mock_redis,
        )

    def _seed_evaluations(self, intent: str, items: list[tuple[float, bool]]):
        with self.Session() as s:
            for score, is_eq in items:
                row = CacheEvaluation(
                    intent=intent,
                    rerank_score=score,
                    is_equivalent=is_eq,
                )
                s.add(row)
            s.commit()

    def test_insufficient_samples_skips_calibration(self):
        self._seed_evaluations("code_generation", [(0.95, True), (0.96, True)])

        res = self.service.calibrate_intent("code_generation", min_samples=10)
        self.assertFalse(res.calibrated)
        self.assertEqual(res.sample_count, 2)
        self.assertIn("Insufficient", res.reason)
        # Keeps default config threshold
        self.assertEqual(
            res.high, config.CACHE_CONFIDENCE_THRESHOLDS["code_generation"]["high"]
        )

    def test_sufficient_samples_calibrates_intent(self):
        # 12 samples for code_generation
        samples = [
            (0.88, False),  # error at 0.88
            (0.90, False),  # error at 0.90
            (0.92, True),
            (0.93, True),
            (0.94, True),
            (0.95, True),
            (0.96, True),
            (0.965, True),
            (0.97, True),
            (0.975, True),
            (0.98, True),
            (0.99, True),
        ]
        self._seed_evaluations("code_generation", samples)

        res = self.service.calibrate_intent(
            "code_generation",
            alpha=0.02,
            moderate_alpha=0.05,
            min_samples=10,
        )
        self.assertTrue(res.calibrated)
        self.assertEqual(res.sample_count, 12)
        # Threshold should be > 0.90 to eliminate the 0.88 and 0.90 errors
        self.assertGreater(res.high, 0.90)
        self.assertLessEqual(res.moderate, res.high)
        self.assertAlmostEqual(res.empirical_error_rate, 0.0, places=2)

    def test_calibrate_all_intents(self):
        self._seed_evaluations("code_generation", [(0.95, True)] * 12)
        self._seed_evaluations(
            "summarization", [(0.90, False)] * 5 + [(0.97, True)] * 10
        )

        all_res = self.service.calibrate_all_intents(min_samples=10)
        self.assertIn("code_generation", all_res)
        self.assertIn("summarization", all_res)
        self.assertTrue(all_res["code_generation"].calibrated)
        self.assertTrue(all_res["summarization"].calibrated)

    async def test_apply_and_query_redis_overrides(self):
        calibrator = IntentCalibrationService(
            session_factory=self.Session,
            redis_service=self.mock_redis,
        )

        self.mock_redis.set = AsyncMock(return_value=True)
        self.mock_redis.redis = MagicMock()
        self.mock_redis.redis.sadd = AsyncMock(return_value=1)

        result = IntentCalibrationResult(
            intent="code_generation",
            high=0.965,
            moderate=0.895,
            sample_count=50,
            error_count=1,
            empirical_error_rate=0.02,
            target_alpha=0.02,
            calibrated=True,
            calibrated_at=datetime.now(UTC),
            applied_to_redis=False,
        )

        applied = await calibrator.apply_overrides_to_redis({"code_generation": result})
        self.assertEqual(applied, 1)
        self.assertTrue(result.applied_to_redis)
        self.mock_redis.set.assert_called_once()
        call_args = self.mock_redis.set.call_args[0]
        self.assertEqual(call_args[0], threshold_redis_key("code_generation"))

        # Test get_redis_override
        stored_payload = call_args[1]
        self.mock_redis.get = AsyncMock(return_value=stored_payload)

        override = await calibrator.get_redis_override("code_generation")
        self.assertIsNotNone(override)
        self.assertEqual(override.intent, "code_generation")
        self.assertEqual(override.high, 0.965)
        self.assertEqual(override.moderate, 0.895)

        # Test delete_redis_override
        self.mock_redis.delete = AsyncMock(return_value=1)
        self.mock_redis.redis.srem = AsyncMock(return_value=1)
        deleted = await calibrator.delete_redis_override("code_generation")
        self.assertTrue(deleted)
        self.mock_redis.delete.assert_called_once_with(
            threshold_redis_key("code_generation")
        )


class TestDynamicConfidenceEvaluation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CacheConfidenceEvaluator.clear_overrides()

    def tearDown(self):
        CacheConfidenceEvaluator.clear_overrides()

    def test_explicit_threshold_override_in_evaluator(self):
        # Default config high threshold for code_generation is 0.95
        # Pass explicit higher threshold: 0.97
        is_hit = CacheConfidenceEvaluator.evaluate(
            rerank_score=0.96,
            request_temperature=0.7,
            request_intent="code_generation",
            thresholds={"high": 0.97, "moderate": 0.90},
        )
        # 0.96 < 0.97 -> rejected by explicit threshold
        self.assertFalse(is_hit)

        # Pass explicit lower threshold: 0.94
        is_hit_lower = CacheConfidenceEvaluator.evaluate(
            rerank_score=0.96,
            request_temperature=0.7,
            request_intent="code_generation",
            thresholds={"high": 0.94, "moderate": 0.90},
        )
        self.assertTrue(is_hit_lower)

    def test_in_memory_override_resolution(self):
        CacheConfidenceEvaluator.set_override(
            "code_generation", {"high": 0.98, "moderate": 0.92}
        )
        th = CacheConfidenceEvaluator.get_thresholds("code_generation")
        self.assertEqual(th["high"], 0.98)
        self.assertEqual(th["moderate"], 0.92)

        is_hit = CacheConfidenceEvaluator.evaluate(
            rerank_score=0.97,
            request_temperature=0.7,
            request_intent="code_generation",
        )
        # 0.97 < 0.98 -> rejected
        self.assertFalse(is_hit)

    async def test_router_service_resolves_redis_override(self):
        mock_redis = MagicMock()
        override_data = {
            "intent": "code_generation",
            "high": 0.968,
            "moderate": 0.899,
            "sample_count": 25,
            "error_count": 0,
            "error_rate": 0.0,
            "alpha": 0.02,
            "calibrated_at": datetime.now(UTC).isoformat(),
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(override_data))

        router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=mock_redis,
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        resolved_th = await router.get_intent_thresholds("code_generation")
        self.assertEqual(resolved_th["high"], 0.968)
        self.assertEqual(resolved_th["moderate"], 0.899)
        mock_redis.get.assert_called_once_with(threshold_redis_key("code_generation"))


class TestCeleryCalibrationTask(unittest.IsolatedAsyncioTestCase):
    async def test_calibrate_task_execution(self):
        with patch(
            "app.modules.cache_audit.tasks.IntentCalibrationService"
        ) as mock_service_cls:
            mock_inst = MagicMock()
            mock_service_cls.return_value = mock_inst
            mock_inst.calibrate_all_intents.return_value = {
                "code_generation": IntentCalibrationResult(
                    intent="code_generation",
                    high=0.95,
                    moderate=0.88,
                    sample_count=20,
                    error_count=0,
                    empirical_error_rate=0.0,
                    target_alpha=0.02,
                    calibrated=True,
                    calibrated_at=datetime.now(UTC),
                )
            }
            mock_inst.apply_overrides_to_redis = AsyncMock(return_value=1)

            res = await _calibrate_intent_thresholds_async()
            self.assertEqual(res["intents_evaluated"], 1)
            self.assertEqual(res["overrides_applied"], 1)
            self.assertTrue(res["results"]["code_generation"]["calibrated"])


if __name__ == "__main__":
    unittest.main()
