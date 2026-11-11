import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import config
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
)
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.intent_classifier.intent_schema import (
    IntentClassificationResult,
    IntentEnum,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.shadow_cache.shadow_cache_schema import ShadowMatchType


def _make_dummy_response(
    content: str = "Test answer", model: str = "gpt-4o-mini"
) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="resp-123",
        object="chat.completion",
        created=1700000000,
        model=model,
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
    )


class TestRouterCachePipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_provider = MagicMock(spec=LlmProviderService)
        self.mock_embedding = MagicMock()
        self.mock_embedding.model_name = "test-embed-v1"
        self.mock_embedding.get_embedding_async = AsyncMock(return_value=[0.1] * 768)
        self.mock_embedding.get_sparse_embedding = MagicMock(
            return_value={"indices": [1], "values": [0.5]}
        )
        self.mock_cache = MagicMock()
        self.mock_redis = MagicMock()
        self.mock_redis.delete = AsyncMock(return_value=1)
        self.mock_reranker = MagicMock()
        self.mock_intent = MagicMock()
        self.mock_entity = MagicMock()
        self.mock_usage = MagicMock()
        self.mock_shadow = MagicMock()
        self.mock_judge = MagicMock()

        self.router = LlmRouterService(
            llm_provider_service=self.mock_provider,
            embedding_service=self.mock_embedding,
            llm_cache_service=self.mock_cache,
            redis_service=self.mock_redis,
            reranker_service=self.mock_reranker,
            intent_classifier_service=self.mock_intent,
            entity_extractor_service=self.mock_entity,
            llm_usage_service=self.mock_usage,
            shadow_cache_service=self.mock_shadow,
            judge_service=self.mock_judge,
        )

    async def asyncTearDown(self):
        await self.router.shutdown()

    # -------------------------------------------------------------------------
    # Redis Exact Match
    # -------------------------------------------------------------------------

    async def test_get_redis_exact_match_disabled(self):
        with patch.object(config, "cache_layer_enabled", return_value=False):
            res = await self.router._get_redis_exact_match(
                "hash1", "openai", force=False
            )
            self.assertIsNone(res)

    async def test_get_redis_exact_match_none_service(self):
        self.router.redis_service = None
        res = await self.router._get_redis_exact_match("hash1", "openai", force=True)
        self.assertIsNone(res)

    async def test_get_redis_exact_match_malformed_json(self):
        self.mock_redis.get = AsyncMock(return_value="not valid json {{{")
        res = await self.router._get_redis_exact_match("hash1", "openai", force=True)
        self.assertIsNone(res)

    async def test_get_redis_exact_match_non_dict_payload(self):
        self.mock_redis.get = AsyncMock(return_value=json.dumps(["not", "a", "dict"]))
        res = await self.router._get_redis_exact_match("hash1", "openai", force=True)
        self.assertIsNone(res)

    async def test_get_redis_exact_match_mock_response_purged(self):
        mock_payload = {
            "response": _make_dummy_response(content="Mock answer").model_dump(),
            "point_id": "p1",
        }
        mock_payload["response"]["system_fingerprint"] = "mock"
        self.mock_redis.get = AsyncMock(return_value=json.dumps(mock_payload))
        self.mock_redis.delete = AsyncMock()

        res = await self.router._get_redis_exact_match("hash1", "openai", force=True)
        self.assertIsNone(res)
        self.mock_redis.delete.assert_awaited_once_with("hash1")

    async def test_get_redis_exact_match_success(self):
        payload = {
            "response": _make_dummy_response(content="Valid answer").model_dump(),
            "point_id": "point-xyz",
        }
        self.mock_redis.get = AsyncMock(return_value=json.dumps(payload))
        res = await self.router._get_redis_exact_match("hash1", "openai", force=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.choices[0].message.content, "Valid answer")
        self.assertEqual(res.cache_info["point_id"], "point-xyz")
        self.assertEqual(res.cache_info["source"], "redis")

    # -------------------------------------------------------------------------
    # Qdrant Exact Match
    # -------------------------------------------------------------------------

    async def test_get_qdrant_exact_match_disabled(self):
        with patch.object(config, "cache_layer_enabled", return_value=False):
            res, pid, ttl = await self.router._get_qdrant_exact_match(
                "h", "t", "p", force=False
            )
            self.assertIsNone(res)

    async def test_get_qdrant_exact_match_none_service(self):
        self.router.llm_cache_service = None
        res, pid, ttl = await self.router._get_qdrant_exact_match(
            "h", "t", "p", force=True
        )
        self.assertIsNone(res)

    async def test_get_qdrant_exact_match_mock_purged(self):
        payload = {
            "response": _make_dummy_response().model_dump(),
            "intent": "general_query",
        }
        payload["response"]["system_fingerprint"] = "mock"
        self.mock_cache.search_exact.return_value = (payload, "point-1")

        res, pid, ttl = await self.router._get_qdrant_exact_match(
            "h", "t", "p", force=True
        )
        self.assertIsNone(res)

    async def test_get_qdrant_exact_match_success(self):
        payload = {
            "response": _make_dummy_response(content="Qdrant hit").model_dump(),
            "intent": "general_query",
            "expires_at": 1800000000,
        }
        self.mock_cache.search_exact.return_value = (payload, "point-qdrant")

        res, pid, ttl = await self.router._get_qdrant_exact_match(
            "h", "t", "p", force=True
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.choices[0].message.content, "Qdrant hit")
        self.assertEqual(pid, "point-qdrant")
        self.assertGreater(ttl, 0)
        self.assertEqual(res.cache_info["source"], "qdrant_exact_path")

    # -------------------------------------------------------------------------
    # Intent Thresholds & Exact Match Delegate
    # -------------------------------------------------------------------------

    async def test_get_exact_match_delegation(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
            tenant_id="tenant-1",
        )
        self.mock_cache.search_exact.return_value = ({"response": {}}, "pt-1")
        payload, pid = await self.router.get_exact_match(req)
        self.assertEqual(pid, "pt-1")

        # Exception degrades gracefully
        self.mock_cache.search_exact.side_effect = RuntimeError("Qdrant socket closed")
        payload_err, pid_err = await self.router.get_exact_match(req)
        self.assertIsNone(payload_err)
        self.assertIsNone(pid_err)

    async def test_get_intent_thresholds_variants(self):
        # Fallback when redis returns None
        self.mock_redis.get = AsyncMock(return_value=None)
        th = await self.router.get_intent_thresholds(IntentEnum.GENERAL_QUERY)
        self.assertIn("high", th)
        self.assertIn("moderate", th)

        # Redis has calibrated threshold
        self.mock_redis.get = AsyncMock(
            return_value=json.dumps({"high": 0.95, "moderate": 0.85})
        )
        th_cal = await self.router.get_intent_thresholds("billing")
        self.assertEqual(th_cal["high"], 0.95)
        self.assertEqual(th_cal["moderate"], 0.85)

        # Redis error falls back
        self.mock_redis.get = AsyncMock(side_effect=RuntimeError("Redis down"))
        th_fallback = await self.router.get_intent_thresholds(None)
        self.assertIn("high", th_fallback)

    # -------------------------------------------------------------------------
    # Save to Redis Cache
    # -------------------------------------------------------------------------

    async def test_save_to_redis_cache_conditions(self):
        resp = _make_dummy_response()
        self.mock_redis.set = AsyncMock()

        # save_to_redis False
        await self.router._save_to_redis_cache("h", resp, "msg", save_to_redis=False)
        self.mock_redis.set.assert_not_called()

        # Mock response skipped
        mock_resp = _make_dummy_response()
        mock_resp.system_fingerprint = "mock"
        await self.router._save_to_redis_cache(
            "h", mock_resp, "msg", save_to_redis=True
        )
        self.mock_redis.set.assert_not_called()

        # ttl is None skipped
        await self.router._save_to_redis_cache(
            "h", resp, "msg", ttl=None, save_to_redis=True
        )
        self.mock_redis.set.assert_not_called()

        # Normal save
        await self.router._save_to_redis_cache(
            "h", resp, "msg", ttl=300, save_to_redis=True, point_id="pt-1"
        )
        self.mock_redis.set.assert_awaited_once()

    # -------------------------------------------------------------------------
    # Evaluate Semantic Cache (Fast Path & RRF)
    # -------------------------------------------------------------------------

    async def test_evaluate_semantic_cache_bypasses(self):
        req_tools = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
        res = await self.router.evaluate_semantic_cache(req_tools, force=True)
        self.assertIsNone(res["response"])

        # Qdrant disabled
        with patch.object(config, "cache_layer_enabled", return_value=False):
            req_plain = ChatCompletionRequest(
                service_name="openai",
                model="gpt-4o-mini",
                messages=[ChatMessage(role="user", content="hello")],
            )
            res_disabled = await self.router.evaluate_semantic_cache(
                req_plain, force=False
            )
            self.assertIsNone(res_disabled["response"])

    async def test_evaluate_semantic_cache_fast_path(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="What is Echo Gate?")],
        )

        cached_resp = _make_dummy_response("Echo Gate is an AI gateway")
        self.mock_cache.query_points_dense.return_value = [
            {
                "id": "fast-point-1",
                "score": 0.98,  # > QDRANT_DENSE_SEARCH_HIGH_THRESHOLD
                "payload": {
                    "prompt": "What is Echo Gate?",
                    "response": cached_resp.model_dump(),
                    "expires_at": 1800000000,
                },
            }
        ]

        with patch.object(config, "QDRANT_DENSE_SEARCH_HIGH_THRESHOLD", 0.90):
            res = await self.router.evaluate_semantic_cache(req, force=True)
            self.assertIsNotNone(res["response"])
            self.assertEqual(res["cache_info"]["source"], "qdrant_fast_path")
            self.assertEqual(res["cache_info"]["point_id"], "fast-point-1")

    async def test_evaluate_semantic_cache_rrf_accepted(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="How do I setup caching?")],
        )

        # Dense miss
        self.mock_cache.query_points_dense.return_value = []

        cached_resp = _make_dummy_response("Setup Redis and Qdrant in config.py")
        self.mock_cache.query_points_rrf.return_value = [
            {
                "id": "rrf-point-1",
                "score": 0.85,
                "payload": {
                    "prompt": "How do I setup caching?",
                    "response": cached_resp.model_dump(),
                    "intent": "general_query",
                },
            }
        ]

        self.mock_intent.classify_intent = AsyncMock(
            return_value=IntentClassificationResult(
                intent=IntentEnum.GENERAL_QUERY,
                time_sensitivity=0.0,
                core_operation="setup",
                core_subject="caching",
            )
        )
        self.mock_intent.calculate_ttl.return_value = 3600
        self.mock_entity.get_qdrant_entity_tags.return_value = []

        # Reranker returns match with high score
        self.mock_reranker.rerank.return_value = [
            {
                "id": "rrf-point-1",
                "score": 0.85,
                "rerank_score": 0.95,
                "payload": {
                    "prompt": "How do I setup caching?",
                    "response": cached_resp.model_dump(),
                },
            }
        ]

        with patch.object(config, "QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD", 0.50):
            res = await self.router.evaluate_semantic_cache(req, force=True)
            self.assertIsNotNone(res["response"])
            self.assertEqual(res["cache_info"]["source"], "qdrant_semantic_path")
            self.assertTrue(res["cache_info"]["cache_hit"])

    async def test_evaluate_semantic_cache_inference_unavailable(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        self.mock_embedding.get_embedding_async = AsyncMock(
            side_effect=InferenceUnavailable("Embedding engine down")
        )
        res = await self.router.evaluate_semantic_cache(req, force=True)
        self.assertIsNone(res["response"])

    # -------------------------------------------------------------------------
    # Save to Qdrant
    # -------------------------------------------------------------------------

    async def test_save_to_qdrant_flow(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Save this")],
            conversation_id="conv-1",
            user_id="user-1",
            tenant_id="tenant-1",
            cache_ttl=600,
            response_format={"type": "json_object"},
            stop=["END"],
        )
        resp = _make_dummy_response("Saved content")
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)

        self.mock_intent.classify_intent = AsyncMock(
            return_value=IntentClassificationResult(
                intent=IntentEnum.GENERAL_QUERY,
                time_sensitivity=0.0,
                core_operation="save",
                core_subject="data",
            )
        )
        self.mock_entity.get_tags_and_max_scope.return_value = (
            ["tag1"],
            CacheScope.GLOBAL,
        )
        self.mock_cache.upsert.return_value = "saved-point-id"

        with patch.object(config, "CACHE_RETENTION_ENABLED", True):
            point_id, ttl = await self.router.save_to_qdrant_cache(
                request=req,
                response=resp,
                service_name="openai",
                model="gpt-4o-mini",
                force=True,
            )
            self.assertEqual(point_id, "saved-point-id")
            self.assertEqual(ttl, 600)
            self.mock_cache.upsert.assert_called_once()

    # -------------------------------------------------------------------------
    # Background Shadow Evaluate
    # -------------------------------------------------------------------------

    async def test_background_shadow_evaluate_matches(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Shadow prompt")],
        )
        resp = _make_dummy_response("Actual provider answer")
        resp.cache_info = {"latency_breakdown": {"provider_ms": 150.0}}

        self.mock_entity.get_tags_and_max_scope.return_value = (
            ["tag"],
            CacheScope.GLOBAL,
        )

        # 1. Redis match
        with (
            patch.object(
                self.router,
                "_get_redis_exact_match",
                AsyncMock(return_value=_make_dummy_response("Actual provider answer")),
            ),
            patch.object(self.router, "_background_save_cache", AsyncMock()),
        ):
            await self.router._background_shadow_evaluate(
                request=req,
                response=resp,
                exact_hash="shadow_hash",
                service_name="openai",
                model="gpt-4o-mini",
            )
            self.mock_shadow.log_evaluation.assert_called()
            eval_create = self.mock_shadow.log_evaluation.call_args[0][0]
            self.assertEqual(eval_create.match_type, ShadowMatchType.REDIS_EXACT)
            self.assertTrue(eval_create.would_have_hit)
            self.assertTrue(eval_create.is_equivalent)

    # -------------------------------------------------------------------------
    # Lookup Serving Cache
    # -------------------------------------------------------------------------

    async def test_lookup_serving_cache_steps(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Serving test")],
        )

        # Redis hit
        with patch.object(
            self.router,
            "_get_redis_exact_match",
            AsyncMock(return_value=_make_dummy_response("Redis hit")),
        ):
            resp, sem = await self.router._lookup_serving_cache(
                exact_hash="h",
                service_name="openai",
                request=req,
                prompt_text="Serving test",
                breakdown={},
            )
            self.assertIsNotNone(resp)
            self.assertEqual(resp.choices[0].message.content, "Redis hit")
            self.assertIsNone(sem)

        # Qdrant exact hit
        with (
            patch.object(
                self.router, "_get_redis_exact_match", AsyncMock(return_value=None)
            ),
            patch.object(
                self.router,
                "_get_qdrant_exact_match",
                AsyncMock(
                    return_value=(_make_dummy_response("Qdrant exact hit"), "pt-1", 300)
                ),
            ),
            patch.object(self.router, "_save_to_redis_cache", AsyncMock()),
        ):
            resp, sem = await self.router._lookup_serving_cache(
                exact_hash="h",
                service_name="openai",
                request=req,
                prompt_text="Serving test",
                breakdown={},
            )
            self.assertIsNotNone(resp)
            self.assertEqual(resp.choices[0].message.content, "Qdrant exact hit")

        # Qdrant semantic hit
        with (
            patch.object(
                self.router, "_get_redis_exact_match", AsyncMock(return_value=None)
            ),
            patch.object(
                self.router,
                "_get_qdrant_exact_match",
                AsyncMock(return_value=(None, None, None)),
            ),
            patch.object(
                self.router,
                "evaluate_semantic_cache",
                AsyncMock(
                    return_value={"response": _make_dummy_response("Semantic hit")}
                ),
            ),
        ):
            resp, sem = await self.router._lookup_serving_cache(
                exact_hash="h",
                service_name="openai",
                request=req,
                prompt_text="Serving test",
                breakdown={},
            )
            self.assertIsNotNone(resp)
            self.assertEqual(resp.choices[0].message.content, "Semantic hit")

    # -------------------------------------------------------------------------
    # Helper Utilities & Audit Tests
    # -------------------------------------------------------------------------

    def test_response_text_variants(self):
        self.assertIsNone(self.router._response_text(None))
        resp_empty = _make_dummy_response()
        resp_empty.choices = []
        self.assertIsNone(self.router._response_text(resp_empty))

        resp_text = _make_dummy_response(content="Hello world")
        self.assertEqual(self.router._response_text(resp_text), "Hello world")

        from app.modules.chat.chat_schema import FunctionCall, ToolCall

        resp_tools = _make_dummy_response()
        resp_tools.choices[0].message.content = None
        resp_tools.choices[0].message.tool_calls = [
            ToolCall(
                id="call_1",
                type="function",
                function=FunctionCall(name="get_weather", arguments="{}"),
            )
        ]
        self.assertIn("get_weather", self.router._response_text(resp_tools))

    def test_should_run_shadow_evaluation(self):
        resp = _make_dummy_response()

        with patch.object(config, "CACHE_MODE", "disabled"):
            self.assertFalse(self.router._should_run_shadow_evaluation(resp))

        with (
            patch.object(config, "CACHE_MODE", "shadow"),
            patch.object(config, "SHADOW_SAMPLE_RATE", 1.0),
        ):
            self.assertTrue(self.router._should_run_shadow_evaluation(resp))

            # Overflowed
            resp_overflow = _make_dummy_response()
            resp_overflow.cache_info = {"overflowed": True}
            self.assertFalse(self.router._should_run_shadow_evaluation(resp_overflow))

            # Mock
            resp_mock = _make_dummy_response()
            resp_mock.system_fingerprint = "mock"
            self.assertFalse(self.router._should_run_shadow_evaluation(resp_mock))

    async def test_await_gateway_log_id(self):
        self.assertIsNone(await self.router._await_gateway_log_id(None))

        async def ok_task():
            return 42

        t = asyncio.create_task(ok_task())
        self.assertEqual(await self.router._await_gateway_log_id(t), 42)

        async def err_task():
            raise RuntimeError("DB failed")

        t_err = asyncio.create_task(err_task())
        self.assertIsNone(await self.router._await_gateway_log_id(t_err))

    def test_should_run_cache_audit(self):
        self.router.cache_audit_service = MagicMock()
        resp = _make_dummy_response()
        resp.cache_info = {"cache_hit": True, "source": "qdrant_semantic_path"}

        with (
            patch.object(config, "CACHE_AUDIT_ENABLED", True),
            patch.object(config, "CACHE_MODE", "active"),
            patch.object(config, "CACHE_AUDIT_SAMPLE_RATE", 1.0),
        ):
            self.assertTrue(self.router._should_run_cache_audit(resp))

            # Not a hit
            resp_no_hit = _make_dummy_response()
            resp_no_hit.cache_info = {"cache_hit": False}
            self.assertFalse(self.router._should_run_cache_audit(resp_no_hit))

    async def test_background_cache_audit_flow(self):
        mock_audit = MagicMock()
        self.router.cache_audit_service = mock_audit

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello audit")],
        )
        cached_resp = _make_dummy_response("cached answer")
        cached_resp.cache_info = {
            "cache_hit": True,
            "source": "qdrant_semantic_path",
            "point_id": "point-audit-1",
            "score": 0.9,
            "rerank_score": 0.95,
        }

        fresh_resp = _make_dummy_response("cached answer")  # Identical -> equivalent

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh_resp, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached_resp,
                exact_hash="audit_hash",
                service_name="openai",
                model="gpt-4o-mini",
            )
            mock_audit.log_audit.assert_called_once()
            eval_create = mock_audit.log_audit.call_args[0][0]
            self.assertTrue(eval_create.is_equivalent)

        # Non-equivalent triggers kill switch
        mock_audit.reset_mock()
        fresh_diff = _make_dummy_response("completely different fresh answer")
        self.router.judge_service = MagicMock()
        self.router.judge_service.evaluate_equivalence = AsyncMock(return_value=False)

        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(fresh_diff, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached_resp,
                exact_hash="audit_hash",
                service_name="openai",
                model="gpt-4o-mini",
            )
            # Eviction should be triggered
            self.mock_cache.delete_point.assert_called_with("point-audit-1")
            self.mock_redis.delete.assert_awaited_once_with("audit_hash")

    async def test_try_serve_cached_instant(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="instant test")],
        )

        with patch.object(config, "CACHE_MODE", "shadow"):
            self.assertIsNone(await self.router.get_instant_cache_hit(req))

        with (
            patch.object(config, "CACHE_MODE", "active"),
            patch.object(
                self.router,
                "_lookup_serving_cache",
                AsyncMock(return_value=(_make_dummy_response("Instant hit"), None)),
            ),
        ):
            resp = await self.router.get_instant_cache_hit(req)
            self.assertIsNotNone(resp)
            self.assertEqual(resp.choices[0].message.content, "Instant hit")

    async def test_generate_completion_cache_hit_and_miss_paths(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello completion")],
        )

        # 1. Cache hit path
        hit_resp = _make_dummy_response("Hit answer")
        hit_resp.cache_info = {"matched": True, "source": "redis", "cache_hit": True}
        with (
            patch.object(
                self.router,
                "_lookup_serving_cache",
                AsyncMock(return_value=(hit_resp, None)),
            ),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            resp = await self.router.generate_completion(req)
            self.assertEqual(resp.choices[0].message.content, "Hit answer")

        # 2. Cache miss -> provider call -> cache write offloaded
        fresh_resp = _make_dummy_response("Fresh provider answer")
        with (
            patch.object(
                self.router, "_lookup_serving_cache", AsyncMock(return_value=(None, {}))
            ),
            patch.object(
                self.router,
                "_call_provider_with_retry",
                AsyncMock(return_value=(fresh_resp, "openai", "gpt-4o-mini")),
            ),
            patch.object(self.router, "_background_save_cache", AsyncMock()),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            resp2 = await self.router.generate_completion(req)
            self.assertEqual(resp2.choices[0].message.content, "Fresh provider answer")

        # 3. Provider returns mock response
        mock_resp = _make_dummy_response("Mock text")
        mock_resp.system_fingerprint = "mock"
        with (
            patch.object(
                self.router, "_lookup_serving_cache", AsyncMock(return_value=(None, {}))
            ),
            patch.object(
                self.router,
                "_call_provider_with_retry",
                AsyncMock(return_value=(mock_resp, "openai", "gpt-4o-mini")),
            ),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            resp_mock = await self.router.generate_completion(req)
            self.assertEqual(resp_mock.cache_info["source"], "mock")

        # 4. Provider raises HTTPException -> re-raised and logged
        from fastapi import HTTPException

        with (
            patch.object(
                self.router, "_lookup_serving_cache", AsyncMock(return_value=(None, {}))
            ),
            patch.object(
                self.router,
                "_call_provider_with_retry",
                AsyncMock(
                    side_effect=HTTPException(
                        status_code=429, detail="Rate limit exceeded"
                    )
                ),
            ),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self.router.generate_completion(req)
            self.assertEqual(ctx.exception.status_code, 429)

        # 5. cache_enabled is False
        req_nocache = req.model_copy()
        req_nocache.cache_enabled = False
        with (
            patch.object(
                self.router, "_lookup_serving_cache", AsyncMock(return_value=(None, {}))
            ),
            patch.object(
                self.router,
                "_call_provider_with_retry",
                AsyncMock(return_value=(fresh_resp, "openai", "gpt-4o-mini")),
            ),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            resp_nocache = await self.router.generate_completion(req_nocache)
            self.assertEqual(
                resp_nocache.choices[0].message.content, "Fresh provider answer"
            )

    async def test_generate_stream_cache_hit_path(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello stream")],
            stream=True,
        )

        hit_resp = _make_dummy_response("Stream cached answer")
        hit_resp.cache_info = {"matched": True, "source": "redis", "cache_hit": True}
        with (
            patch.object(
                self.router,
                "_lookup_serving_cache",
                AsyncMock(return_value=(hit_resp, None)),
            ),
            patch.object(config, "CACHE_MODE", "active"),
        ):
            chunks = [c async for c in self.router.generate_stream(req)]
            self.assertGreater(len(chunks), 0)

    async def test_background_cache_audit_empty_fresh_and_eviction_exceptions(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="audit test empty")],
        )
        cached_resp = _make_dummy_response("Cached answer")

        # 1. Empty fresh text returns early
        empty_resp = _make_dummy_response("")
        with patch.object(
            self.router,
            "_call_provider_with_retry",
            AsyncMock(return_value=(empty_resp, "openai", "gpt-4o-mini")),
        ):
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached_resp,
                exact_hash="empty_hash",
                service_name="openai",
                model="gpt-4o-mini",
            )

        # 2. Eviction exceptions from Qdrant and Redis are safely caught
        fresh_diff = _make_dummy_response("Different text")
        self.router.judge_service = MagicMock()
        self.router.judge_service.evaluate_equivalence = AsyncMock(return_value=False)
        self.mock_cache.delete_point = MagicMock(
            side_effect=RuntimeError("Qdrant drop")
        )
        self.mock_redis.delete = AsyncMock(side_effect=RuntimeError("Redis down"))

        with (
            patch.object(
                self.router,
                "_call_provider_with_retry",
                AsyncMock(return_value=(fresh_diff, "openai", "gpt-4o-mini")),
            ),
            patch.object(config, "CACHE_AUDIT_EVICT_ON_FAILURE", True),
        ):
            # Should not raise
            await self.router._background_cache_audit(
                request=req,
                cached_response=cached_resp,
                exact_hash="err_hash",
                service_name="openai",
                model="gpt-4o-mini",
            )

    async def test_call_streaming_provider_with_retry_budget_and_breaker(self):
        from fastapi import HTTPException

        from app.core.circuit_breaker import CircuitBreakerOpenException

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="stream budget")],
        )

        # 1. Timeout budget exceeded
        with self.assertRaises(HTTPException) as ctx:
            async for _ in self.router._stream_from_provider_with_fallback(
                request=req,
                service_name="openai",
                timeout_budget_ms=10,
            ):
                pass
        self.assertEqual(ctx.exception.status_code, 504)

        # 2. Circuit breaker open on only candidate
        mock_breaker = MagicMock()
        mock_breaker.can_execute.return_value = False
        mock_breaker.remaining_recovery_time.return_value = 30.0

        with (
            patch(
                "app.core.circuit_breaker.circuit_breaker_registry.get_breaker",
                return_value=mock_breaker,
            ),
            patch.object(config, "USE_FALLBACK_LLM", "false"),
        ):
            with self.assertRaises(CircuitBreakerOpenException):
                async for _ in self.router._stream_from_provider_with_fallback(
                    request=req,
                    service_name="openai",
                ):
                    pass

        # 3. StopAsyncIteration on first chunk returns cleanly
        mock_breaker.can_execute.return_value = True

        async def empty_stream(*args, **kwargs):
            if False:
                yield None

        with (
            patch(
                "app.core.circuit_breaker.circuit_breaker_registry.get_breaker",
                return_value=mock_breaker,
            ),
            patch.object(
                self.router.llm_provider_service,
                "generate_stream",
                return_value=empty_stream(),
            ),
            patch.object(config, "USE_FALLBACK_LLM", "false"),
        ):
            chunks = [
                c
                async for c in self.router._stream_from_provider_with_fallback(
                    request=req,
                    service_name="openai",
                )
            ]
            self.assertEqual(len(chunks), 0)


if __name__ == "__main__":
    unittest.main()
