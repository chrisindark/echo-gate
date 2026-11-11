import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.gateway_requests.gateway_requests_schema import (
    EvaluationStatus,
    GatewayRequestLogCreate,
    GatewayRequestLogUpdate,
    RoutingDecision,
)
from app.modules.gateway_requests.gateway_requests_service import GatewayRequestsService


class TestGatewayRequestsServiceUnit(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = GatewayRequestsService(
            session_factory=self.Session,
            session_factory_read=self.Session,
        )

    def tearDown(self):
        self.engine.dispose()

    def _sample_log(self, **kwargs) -> GatewayRequestLogCreate:
        default_data = {
            "query_text": "What is the speed of light?",
            "response_text": "About 300,000 km/s",
            "routing_decision": RoutingDecision.LLM,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "exact_hash": "hash_light_123",
            "tenant_id": "tenant-test",
            "user_id": "user-test",
            "latency_ms": 150,
            "evaluation_status": EvaluationStatus.PENDING,
        }
        default_data.update(kwargs)
        return GatewayRequestLogCreate(**default_data)

    def test_log_request_and_get_by_id_and_hash(self):
        log_create = self._sample_log()
        created = self.service.log_request(log_create)
        self.assertIsNotNone(created.id)
        self.assertEqual(created.query_text, "What is the speed of light?")

        # get_request_log by ID
        fetched = self.service.get_request_log(created.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, created.id)
        self.assertIsNone(self.service.get_request_log(99999))

        # get_request_log_by_hash
        by_hash = self.service.get_request_log_by_hash("hash_light_123")
        self.assertIsNotNone(by_hash)
        self.assertEqual(by_hash.id, created.id)
        self.assertIsNone(self.service.get_request_log_by_hash("nonexistent_hash"))

    def test_log_request_exception_rollback(self):
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.commit.side_effect = RuntimeError("DB write error")
        svc = GatewayRequestsService(
            session_factory=MagicMock(return_value=mock_session)
        )
        with self.assertRaises(RuntimeError):
            svc.log_request(self._sample_log())
        mock_session.rollback.assert_called_once()

    def test_get_request_logs_with_all_filters(self):
        now = datetime.now(UTC)
        # Seed 3 logs
        self.service.log_request(
            self._sample_log(
                query_text="alpha query",
                tenant_id="t1",
                user_id="u1",
                routing_decision=RoutingDecision.LLM,
                provider="openai",
                model="gpt-4o",
                latency_ms=100,
                evaluation_status=EvaluationStatus.PENDING,
            )
        )
        self.service.log_request(
            self._sample_log(
                query_text="beta query",
                tenant_id="t2",
                user_id="u2",
                routing_decision=RoutingDecision.QDRANT_SEMANTIC,
                provider="groq",
                model="llama-3",
                latency_ms=250,
                evaluation_status=EvaluationStatus.EVALUATED,
                error_message="some error",
            )
        )
        self.service.log_request(
            self._sample_log(
                query_text="gamma query",
                tenant_id="t1",
                user_id="u2",
                routing_decision=RoutingDecision.REDIS_EXACT,
                provider="mock",
                model="mock-model",
                latency_ms=5,
                evaluation_status=EvaluationStatus.SKIPPED,
            )
        )

        # 1. Tenant filter
        items, count = self.service.get_request_logs(tenant_id="t1")
        self.assertEqual(count, 2)

        # 2. User filter
        items, count = self.service.get_request_logs(user_id="u2")
        self.assertEqual(count, 2)

        # 3. Routing decision filter
        items, count = self.service.get_request_logs(routing_decision="LLM")
        self.assertEqual(count, 1)

        # 4. Provider & model filter
        items, count = self.service.get_request_logs(provider="groq", model="llama-3")
        self.assertEqual(count, 1)

        # 5. Evaluation status filter
        items, count = self.service.get_request_logs(evaluation_status="PENDING")
        self.assertEqual(count, 1)

        # 6. has_error filter
        items, count = self.service.get_request_logs(has_error=True)
        self.assertEqual(count, 1)
        items, count = self.service.get_request_logs(has_error=False)
        self.assertEqual(count, 2)

        # 7. Latency range filter
        items, count = self.service.get_request_logs(
            min_latency_ms=50, max_latency_ms=200
        )
        self.assertEqual(count, 1)

        # 8. Search query filter
        items, count = self.service.get_request_logs(search="beta")
        self.assertEqual(count, 1)

        # 9. Date filters
        items, count = self.service.get_request_logs(
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        self.assertEqual(count, 3)

        # 10. Pagination
        items, count = self.service.get_request_logs(skip=0, limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(count, 3)

    def test_get_pending_evaluations(self):
        # 1. PENDING and LLM
        self.service.log_request(
            self._sample_log(
                routing_decision=RoutingDecision.LLM,
                evaluation_status=EvaluationStatus.PENDING,
            )
        )
        # 2. PENDING and QDRANT_SEMANTIC
        self.service.log_request(
            self._sample_log(
                routing_decision=RoutingDecision.QDRANT_SEMANTIC,
                evaluation_status=EvaluationStatus.PENDING,
            )
        )
        # 3. PENDING but REDIS_EXACT (should be excluded)
        self.service.log_request(
            self._sample_log(
                routing_decision=RoutingDecision.REDIS_EXACT,
                evaluation_status=EvaluationStatus.PENDING,
            )
        )
        # 4. EVALUATED and LLM (should be excluded)
        self.service.log_request(
            self._sample_log(
                routing_decision=RoutingDecision.LLM,
                evaluation_status=EvaluationStatus.EVALUATED,
            )
        )

        pending = self.service.get_pending_evaluations(limit=10)
        self.assertEqual(len(pending), 2)

    def test_update_evaluation(self):
        created = self.service.log_request(self._sample_log())
        update_data = GatewayRequestLogUpdate(
            evaluation_status=EvaluationStatus.EVALUATED,
            llm_relevance_score=0.95,
            is_false_positive=False,
        )

        updated = self.service.update_evaluation(created.id, update_data)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.evaluation_status, "EVALUATED")
        self.assertAlmostEqual(float(updated.llm_relevance_score), 0.95)
        self.assertFalse(updated.is_false_positive)

        # Nonexistent log ID returns None
        self.assertIsNone(self.service.update_evaluation(99999, update_data))

    def test_delete_request_log(self):
        created = self.service.log_request(self._sample_log())
        self.assertTrue(self.service.delete_request_log(created.id))
        self.assertIsNone(self.service.get_request_log(created.id))

        # Deleting nonexistent log returns False
        self.assertFalse(self.service.delete_request_log(99999))

    def test_session_init_variants(self):
        with self.Session() as s:
            svc = GatewayRequestsService(session_factory=s, session_factory_read=s)
            self.assertEqual(svc._db_session, s)
            self.assertEqual(svc._db_session_read, s)
