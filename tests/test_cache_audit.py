"""Unit tests for the Phase 2 Cache Audit Loop.

Covers:
- Probabilistic gating on semantic cache hits in CACHE_MODE=active (_should_run_cache_audit)
- Persistence in SQLite (CacheEvaluation model, CacheAuditService log/get/summary)
- End-to-end background audit execution (_background_cache_audit)
- Equivalence evaluation via JudgeService
- Instant eviction kill switch (Qdrant point deletion + Redis exact key purge) on non-equivalence
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import circuit_breaker_registry
from app.core.config import config
from app.core.database import Base
from app.modules.cache_audit.cache_audit_model import CacheEvaluation  # noqa: F401
from app.modules.cache_audit.cache_audit_schema import (
    CacheAuditCreate,
    CacheAuditSummary,
)
from app.modules.cache_audit.cache_audit_service import CacheAuditService
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


def _response(
    content: str = "cached answer",
    source: str = "qdrant_semantic_path",
    cache_hit: bool = True,
    point_id: str | None = "point-123",
) -> ChatCompletionResponse:
    res = ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model="gpt-4o-mini",
        choices=[
            Choice(index=0, message=ChatMessage(role="assistant", content=content))
        ],
    )
    res.cache_info = {
        "cache_hit": cache_hit,
        "source": source,
        "score": 0.95,
        "rerank_score": 0.92,
        "point_id": point_id,
        "intent": "general_knowledge",
        "latency_breakdown": {"cache_lookup_total_ms": 12.5},
    }
    return res


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name="openai",
        model="gpt-4o-mini",
        tenant_id="tenant-alpha",
        user_id="user-1",
        messages=[ChatMessage(role="user", content="How do I reset my password?")],
    )


class TestCacheAuditGating(unittest.TestCase):
    def setUp(self):
        self._saved_mode = config.CACHE_MODE
        self._saved_enabled = config.CACHE_AUDIT_ENABLED
        self._saved_sample_rate = config.CACHE_AUDIT_SAMPLE_RATE

        config.CACHE_MODE = "active"
        config.CACHE_AUDIT_ENABLED = True
        config.CACHE_AUDIT_SAMPLE_RATE = 1.0

        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
            cache_audit_service=MagicMock(),
        )

    def tearDown(self):
        config.CACHE_MODE = self._saved_mode
        config.CACHE_AUDIT_ENABLED = self._saved_enabled
        config.CACHE_AUDIT_SAMPLE_RATE = self._saved_sample_rate

    def test_gate_returns_true_for_sampled_semantic_cache_hit(self):
        res = _response(source="qdrant_semantic_path")
        self.assertTrue(self.router._should_run_cache_audit(res))

    def test_gate_returns_true_for_sampled_fast_path_cache_hit(self):
        res = _response(source="qdrant_fast_path")
        self.assertTrue(self.router._should_run_cache_audit(res))

    def test_gate_returns_false_when_audit_disabled(self):
        config.CACHE_AUDIT_ENABLED = False
        res = _response(source="qdrant_semantic_path")
        self.assertFalse(self.router._should_run_cache_audit(res))

    def test_gate_returns_false_when_not_active_mode(self):
        config.CACHE_MODE = "shadow"
        res = _response(source="qdrant_semantic_path")
        self.assertFalse(self.router._should_run_cache_audit(res))

        config.CACHE_MODE = "disabled"
        self.assertFalse(self.router._should_run_cache_audit(res))

    def test_gate_returns_false_when_service_missing(self):
        self.router.cache_audit_service = None
        res = _response(source="qdrant_semantic_path")
        self.assertFalse(self.router._should_run_cache_audit(res))

    def test_gate_returns_false_for_exact_match(self):
        # Exact hash matches do not suffer from vector/threshold false-positive drift
        res_redis = _response(source="redis")
        self.assertFalse(self.router._should_run_cache_audit(res_redis))

        res_qdrant_exact = _response(source="qdrant_exact_path")
        self.assertFalse(self.router._should_run_cache_audit(res_qdrant_exact))

    def test_gate_returns_false_for_cache_miss(self):
        res = _response(source="llm", cache_hit=False)
        self.assertFalse(self.router._should_run_cache_audit(res))

    def test_gate_respects_zero_sample_rate(self):
        config.CACHE_AUDIT_SAMPLE_RATE = 0.0
        res = _response(source="qdrant_semantic_path")
        self.assertFalse(self.router._should_run_cache_audit(res))


class TestCacheAuditServicePersistence(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = CacheAuditService(
            session_factory=self.Session, session_factory_read=self.Session
        )

    def test_log_and_query_audits(self):
        audit_1 = CacheAuditCreate(
            exact_hash="hash-1",
            tenant_id="tenant-alpha",
            model="gpt-4o-mini",
            provider="openai",
            match_type="qdrant_semantic_path",
            query_text="reset password",
            cached_answer="click reset link",
            fresh_answer="click reset link",
            is_equivalent=True,
            point_id="point-1",
            latency_saved_ms=250,
            evicted=False,
        )
        audit_2 = CacheAuditCreate(
            exact_hash="hash-2",
            tenant_id="tenant-alpha",
            model="gpt-4o-mini",
            provider="openai",
            match_type="qdrant_semantic_path",
            query_text="delete account",
            cached_answer="click reset link",
            fresh_answer="go to settings and click delete",
            is_equivalent=False,
            point_id="point-2",
            latency_saved_ms=300,
            evicted=True,
        )
        self.service.log_audit(audit_1)
        self.service.log_audit(audit_2)

        # Query all
        records, total = self.service.get_audits(tenant_id="tenant-alpha")
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 2)

        # Query evicted only
        evicted_records, evicted_total = self.service.get_audits(
            tenant_id="tenant-alpha", evicted=True
        )
        self.assertEqual(evicted_total, 1)
        self.assertEqual(evicted_records[0].point_id, "point-2")

        # Query non-equivalent only
        mismatch_records, mismatch_total = self.service.get_audits(
            tenant_id="tenant-alpha", is_equivalent=False
        )
        self.assertEqual(mismatch_total, 1)
        self.assertEqual(mismatch_records[0].exact_hash, "hash-2")

    def test_summary_aggregation(self):
        self.service.log_audit(
            CacheAuditCreate(
                tenant_id="tenant-alpha",
                is_equivalent=True,
                evicted=False,
            )
        )
        self.service.log_audit(
            CacheAuditCreate(
                tenant_id="tenant-alpha",
                is_equivalent=True,
                evicted=False,
            )
        )
        self.service.log_audit(
            CacheAuditCreate(
                tenant_id="tenant-alpha",
                is_equivalent=False,
                evicted=True,
            )
        )
        self.service.log_audit(
            CacheAuditCreate(
                tenant_id="tenant-alpha",
                is_equivalent=None,
                evicted=False,
            )
        )

        summary: CacheAuditSummary = self.service.get_audit_summary(
            tenant_id="tenant-alpha"
        )
        self.assertEqual(summary.total_audits, 4)
        self.assertEqual(summary.equivalent_count, 2)
        self.assertEqual(summary.non_equivalent_count, 1)
        self.assertEqual(summary.uncompared_count, 1)
        self.assertEqual(summary.evicted_count, 1)
        # 2 / (2 + 1) = 66.7%
        self.assertAlmostEqual(summary.equivalence_rate, 2 / 3, places=3)


class TestBackgroundCacheAuditExecution(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

        self._saved_mode = config.CACHE_MODE
        self._saved_enabled = config.CACHE_AUDIT_ENABLED
        self._saved_evict = config.CACHE_AUDIT_EVICT_ON_FAILURE
        self._saved_sample_rate = config.CACHE_AUDIT_SAMPLE_RATE

        config.CACHE_MODE = "active"
        config.CACHE_AUDIT_ENABLED = True
        config.CACHE_AUDIT_EVICT_ON_FAILURE = True
        config.CACHE_AUDIT_SAMPLE_RATE = 1.0

        self.mock_provider = MagicMock()
        self.mock_qdrant = MagicMock()
        self.mock_redis = MagicMock()
        self.mock_redis.delete = AsyncMock(return_value=1)
        self.mock_judge = MagicMock()
        self.mock_audit_service = MagicMock()

        self.router = LlmRouterService(
            llm_provider_service=self.mock_provider,
            embedding_service=MagicMock(),
            llm_cache_service=self.mock_qdrant,
            redis_service=self.mock_redis,
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
            cache_audit_service=self.mock_audit_service,
            judge_service=self.mock_judge,
        )

    def tearDown(self):
        config.CACHE_MODE = self._saved_mode
        config.CACHE_AUDIT_ENABLED = self._saved_enabled
        config.CACHE_AUDIT_EVICT_ON_FAILURE = self._saved_evict
        config.CACHE_AUDIT_SAMPLE_RATE = self._saved_sample_rate

    async def test_audit_identical_answer_no_eviction(self):
        """When cached answer exactly matches fresh provider answer, audit records equivalent and does not evict."""
        req = _request()
        cached = _response(content="Visit settings to change password.")

        fresh = ChatCompletionResponse(
            id="chatcmpl-fresh",
            created=1,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Visit settings to change password.",
                    ),
                )
            ],
        )

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached,
                exact_hash="hash-123",
                service_name="openai",
                model="gpt-4o-mini",
                gateway_log_task=asyncio.create_task(asyncio.sleep(0, result=99)),
            )

        self.mock_qdrant.delete_point.assert_not_called()
        self.mock_redis.delete.assert_not_called()
        self.mock_judge.evaluate_equivalence.assert_not_called()

        self.mock_audit_service.log_audit.assert_called_once()
        logged_audit: CacheAuditCreate = self.mock_audit_service.log_audit.call_args[0][
            0
        ]
        self.assertEqual(logged_audit.request_id, 99)
        self.assertEqual(logged_audit.exact_hash, "hash-123")
        self.assertTrue(logged_audit.is_equivalent)
        self.assertFalse(logged_audit.evicted)

    async def test_audit_different_answer_judged_equivalent(self):
        """When fresh answer differs in wording but judge says YES, entry is kept."""
        req = _request()
        cached = _response(content="Go to Account > Security to change password.")

        fresh = ChatCompletionResponse(
            id="chatcmpl-fresh",
            created=1,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Navigate to your Profile settings and select password reset.",
                    ),
                )
            ],
        )

        self.mock_judge.evaluate_equivalence = AsyncMock(return_value=True)

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached,
                exact_hash="hash-123",
                service_name="openai",
                model="gpt-4o-mini",
                gateway_log_task=asyncio.create_task(asyncio.sleep(0, result=101)),
            )

        self.mock_judge.evaluate_equivalence.assert_called_once()
        self.mock_qdrant.delete_point.assert_not_called()
        self.mock_redis.delete.assert_not_called()

        logged_audit: CacheAuditCreate = self.mock_audit_service.log_audit.call_args[0][
            0
        ]
        self.assertTrue(logged_audit.is_equivalent)
        self.assertFalse(logged_audit.evicted)

    async def test_audit_judged_non_equivalent_triggers_instant_eviction(self):
        """When judge says NO, entry is false-positive/poisoned: instantly purge from Qdrant and Redis."""
        req = _request()
        cached = _response(
            content="To cancel your subscription, contact billing.",
            point_id="point-poisoned-999",
        )

        fresh = ChatCompletionResponse(
            id="chatcmpl-fresh",
            created=1,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="To reset your password, click 'Forgot Password' on the login screen.",
                    ),
                )
            ],
        )

        self.mock_judge.evaluate_equivalence = AsyncMock(return_value=False)

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached,
                exact_hash="hash-poisoned",
                service_name="openai",
                model="gpt-4o-mini",
                gateway_log_task=asyncio.create_task(asyncio.sleep(0, result=102)),
            )

        # Instant eviction kill switch triggered
        self.mock_qdrant.delete_point.assert_called_once_with("point-poisoned-999")
        self.mock_redis.delete.assert_called_once_with("hash-poisoned")

        logged_audit: CacheAuditCreate = self.mock_audit_service.log_audit.call_args[0][
            0
        ]
        self.assertFalse(logged_audit.is_equivalent)
        self.assertTrue(logged_audit.evicted)

    async def test_audit_non_equivalent_without_eviction_flag(self):
        """When CACHE_AUDIT_EVICT_ON_FAILURE=False, non-equivalence is recorded but neither Qdrant nor Redis is purged."""
        config.CACHE_AUDIT_EVICT_ON_FAILURE = False

        req = _request()
        cached = _response(content="Wrong answer.", point_id="point-keep-1")

        fresh = ChatCompletionResponse(
            id="chatcmpl-fresh",
            created=1,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Right answer."),
                )
            ],
        )

        self.mock_judge.evaluate_equivalence = AsyncMock(return_value=False)

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached,
                exact_hash="hash-keep",
                service_name="openai",
                model="gpt-4o-mini",
            )

        self.mock_qdrant.delete_point.assert_not_called()
        self.mock_redis.delete.assert_not_called()

        logged_audit: CacheAuditCreate = self.mock_audit_service.log_audit.call_args[0][
            0
        ]
        self.assertFalse(logged_audit.is_equivalent)
        self.assertFalse(logged_audit.evicted)

    async def test_audit_provider_failure_is_handled_gracefully(self):
        """When the provider fails to return a fresh response during audit, nothing crashes or is evicted."""
        req = _request()
        cached = _response(content="Cached answer.")

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(side_effect=RuntimeError("Provider timeout")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached,
                exact_hash="hash-err",
                service_name="openai",
                model="gpt-4o-mini",
            )

        self.mock_qdrant.delete_point.assert_not_called()
        self.mock_redis.delete.assert_not_called()
        self.mock_audit_service.log_audit.assert_not_called()

    async def test_generate_completion_spawns_cache_audit_on_semantic_hit(self):
        """In active mode, a semantic hit serves the cached answer and spawns _background_cache_audit."""
        req = _request()
        cached = _response(content="Cached response from Qdrant semantic search.")

        self.router._get_redis_exact_match = AsyncMock(return_value=None)
        self.router._get_qdrant_exact_match = AsyncMock(return_value=(None, None, None))
        self.router.evaluate_semantic_cache = AsyncMock(
            return_value={"response": cached, "latency_breakdown": {}}
        )

        with patch.object(
            self.router, "_background_cache_audit", AsyncMock()
        ) as mock_audit:
            response = await self.router.generate_completion(req)
            self.assertEqual(
                response.choices[0].message.content,
                "Cached response from Qdrant semantic search.",
            )
            # Give background task event-loop tick to fire
            await asyncio.sleep(0.01)
            mock_audit.assert_called_once()
            call_kwargs = mock_audit.call_args[1]
            self.assertEqual(call_kwargs["request"], req)
            self.assertEqual(
                call_kwargs["cached_response"].choices[0].message.content,
                "Cached response from Qdrant semantic search.",
            )

    async def test_generate_stream_spawns_cache_audit_on_semantic_hit(self):
        """In active mode, a streamed semantic hit serves chunks and spawns _background_cache_audit."""
        req = _request()
        req.stream = True
        cached = _response(content="Cached streamed answer.")

        self.router._get_redis_exact_match = AsyncMock(return_value=None)
        self.router._get_qdrant_exact_match = AsyncMock(return_value=(None, None, None))
        self.router.evaluate_semantic_cache = AsyncMock(
            return_value={"response": cached, "latency_breakdown": {}}
        )

        with patch.object(
            self.router, "_background_cache_audit", AsyncMock()
        ) as mock_audit:
            chunks = []
            async for chunk in self.router.generate_stream(req):
                chunks.append(chunk)

            self.assertTrue(len(chunks) > 0)
            await asyncio.sleep(0.01)
            mock_audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
