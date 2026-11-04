"""Live-audit calibration source for ``app.scripts.calibrate_reranker``.

The audit source exists so thresholds can be calibrated from the ground truth
the shadow audit actually accumulated, instead of only from the static test
dataset. These tests pin the two pieces that make that possible: reading
compared ``cache_evaluations`` rows and turning them into per-intent conformal
recommendations.
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import config
from app.core.database import Base
from app.modules.cache_audit.cache_audit_model import CacheEvaluation  # noqa: F401
from app.modules.cache_audit.cache_audit_schema import CacheAuditCreate
from app.modules.cache_audit.cache_audit_service import CacheAuditService
from app.modules.gateway_requests.gateway_requests_model import (  # noqa: F401
    GatewayRequestLog,
)
from app.scripts.calibrate_reranker import (
    _load_live_audit_rows,
    _per_intent_calibration,
)


class LiveAuditRowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = CacheAuditService(
            session_factory=self.Session, session_factory_read=self.Session
        )

    @contextmanager
    def _session(self):
        with self.Session() as session:
            yield session

    def _load(self, **kwargs):
        with patch("app.core.database.task_db_session", self._session):
            return _load_live_audit_rows(**kwargs)

    def test_only_compared_rows_are_read(self):
        self.service.log_audit(
            CacheAuditCreate(
                intent="code_generation",
                model="gpt-4o-mini",
                rerank_score=0.91,
                vector_similarity=0.94,
                is_equivalent=True,
            )
        )
        # No verdict: still pending, not ground truth.
        self.service.log_audit(
            CacheAuditCreate(intent="code_generation", rerank_score=0.88)
        )
        # No rerank score: cannot be swept.
        self.service.log_audit(
            CacheAuditCreate(intent="code_generation", is_equivalent=False)
        )

        rows = self._load()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["intent"], "code_generation")
        self.assertEqual(rows[0]["model"], "gpt-4o-mini")
        self.assertAlmostEqual(rows[0]["rerank_score"], 0.91)
        self.assertAlmostEqual(rows[0]["vector_similarity"], 0.94)
        self.assertTrue(rows[0]["is_equivalent"])
        self.assertIsNotNone(rows[0]["created_at"])

    def test_intent_and_model_filters_narrow_the_sweep(self):
        for intent, model, score, equivalent in (
            ("code_generation", "gpt-4o-mini", 0.95, True),
            ("code_generation", "gemini-3.1-flash-lite", 0.97, True),
            ("general_query", "gpt-4o-mini", 0.72, False),
        ):
            self.service.log_audit(
                CacheAuditCreate(
                    intent=intent,
                    model=model,
                    rerank_score=score,
                    is_equivalent=equivalent,
                )
            )

        self.assertEqual(len(self._load(intent="code_generation")), 2)
        self.assertEqual(len(self._load(model="gpt-4o-mini")), 2)
        rows = self._load(intent="general_query", model="gpt-4o-mini")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_equivalent"])

    def test_limit_caps_the_number_of_rows(self):
        for score in (0.70, 0.75, 0.80):
            self.service.log_audit(
                CacheAuditCreate(rerank_score=score, is_equivalent=False)
            )

        self.assertEqual(len(self._load(limit=2)), 2)


class PerIntentCalibrationTests(unittest.TestCase):
    def _rows(self):
        return [
            # False positive at 0.80: the recommendation must exclude it.
            {"intent": "code_generation", "rerank_score": 0.80, "is_equivalent": False},
            {"intent": "code_generation", "rerank_score": 0.95, "is_equivalent": True},
            {"intent": "code_generation", "rerank_score": 0.99, "is_equivalent": True},
            # Too few samples to calibrate.
            {"intent": "general_query", "rerank_score": 0.90, "is_equivalent": True},
            # Audits without an intent are still reported.
            {"intent": None, "rerank_score": 0.93, "is_equivalent": True},
        ]

    def test_recommendation_excludes_the_false_positive(self):
        results = _per_intent_calibration(
            self._rows(), alpha=0.02, moderate_alpha=0.05, min_samples=2
        )

        code = results["code_generation"]
        self.assertTrue(code["calibrated"])
        self.assertEqual(code["samples"], 3)
        self.assertEqual(code["errors"], 1)
        self.assertGreater(code["high"], 0.80)
        self.assertLessEqual(code["high"], 0.95)
        self.assertLessEqual(code["moderate"], code["high"])
        self.assertIsNone(code["reason"])

    def test_intents_below_min_samples_keep_the_configured_defaults(self):
        results = _per_intent_calibration(
            self._rows(), alpha=0.02, moderate_alpha=0.05, min_samples=3
        )

        general = results["general_query"]
        self.assertFalse(general["calibrated"])
        self.assertEqual(general["samples"], 1)
        self.assertIn("Insufficient samples", general["reason"])
        self.assertEqual(
            general["high"], config.CACHE_CONFIDENCE_THRESHOLDS["general_query"]["high"]
        )
        self.assertEqual(
            general["moderate"],
            config.CACHE_CONFIDENCE_THRESHOLDS["general_query"]["moderate"],
        )

    def test_rows_without_an_intent_are_grouped_as_unclassified(self):
        results = _per_intent_calibration(
            self._rows(), alpha=0.02, moderate_alpha=0.05, min_samples=1
        )

        self.assertIn("unclassified", results)
        self.assertEqual(results["unclassified"]["samples"], 1)
        self.assertTrue(results["unclassified"]["calibrated"])

    def test_no_rows_produces_no_intents(self):
        self.assertEqual(
            _per_intent_calibration(
                [], alpha=0.02, moderate_alpha=0.05, min_samples=10
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
