"""Tests for step-by-step external service initialization and gating."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from app.core.config import config
from app.core.dependencies import DependencyContainer
from app.main import app
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.health.health_service import check_celery_broker, check_database
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.redis.redis_service import RedisService

_ENV_KEYS = (
    "CACHE_MODE",
    "REDIS_ENABLED",
    "REDIS_URL",
    "USE_REDIS_EXACT_MATCHING",
    "QDRANT_ENABLED",
    "QDRANT_URL",
    "USE_QDRANT_EXACT_MATCHING",
    "USE_QDRANT_SEMANTIC_MATCHING",
    "INFERENCE_ENABLED",
    "INFERENCE_BASE_URL",
    "INFERENCE_EMBED_URL",
    "INFERENCE_RERANK_URL",
    "INFERENCE_RELEVANCE_URL",
    "INFERENCE_NLI_URL",
    "INFERENCE_COMPRESS_URL",
    "SHADOW_CACHE_ENABLED",
    "SHADOW_JUDGE_ENABLED",
    "CACHE_AUDIT_ENABLED",
)


class _EnvIsolatedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._reset_container()

    def tearDown(self):
        self._reset_container()
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.load()

    @staticmethod
    def _reset_container() -> None:
        DependencyContainer._embedding_service = None
        DependencyContainer._qdrant_client_service = None
        DependencyContainer._llm_cache_collection_service = None
        DependencyContainer._llm_provider_service = None
        DependencyContainer._llm_router_service = None
        DependencyContainer._redis_service = None
        DependencyContainer._reranker_service = None
        DependencyContainer._intent_classifier_service = None
        DependencyContainer._entity_extractor_service = None
        DependencyContainer._llm_usage_service = None
        DependencyContainer._llm_quota_service = None
        DependencyContainer._llm_quota_rule_service = None
        DependencyContainer._cross_encoder_service = None
        DependencyContainer._judge_service = None
        DependencyContainer._instruction_service = None
        DependencyContainer._gateway_requests_service = None
        DependencyContainer._shadow_cache_service = None
        DependencyContainer._cache_audit_service = None
        DependencyContainer._intent_calibration_service = None
        DependencyContainer._inference_client = None
        DependencyContainer._prompt_optimizer = None

    def _load_env(self, **overrides: str) -> None:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in overrides.items():
            os.environ[k] = v
        config.load()


class TestStepByStepServiceInit(_EnvIsolatedTest):
    def test_disabled_cache_mode_turns_everything_off(self):
        self._load_env(
            CACHE_MODE="disabled",
            REDIS_ENABLED="true",
            QDRANT_ENABLED="true",
            INFERENCE_ENABLED="true",
            INFERENCE_BASE_URL="http://localhost:7997",
            USE_REDIS_EXACT_MATCHING="true",
            USE_QDRANT_EXACT_MATCHING="true",
            USE_QDRANT_SEMANTIC_MATCHING="true",
        )
        self.assertFalse(config.cache_enabled)
        self.assertFalse(config.redis_cache_enabled)
        self.assertFalse(config.qdrant_cache_enabled)
        self.assertFalse(config.inference_cache_enabled)
        self.assertFalse(config.shadow_cache_enabled)
        self.assertFalse(config.cache_audit_enabled)

    def test_shadow_mode_with_services_off_matches_llm_only(self):
        self._load_env(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="false",
            USE_QDRANT_EXACT_MATCHING="false",
            USE_QDRANT_SEMANTIC_MATCHING="false",
            INFERENCE_BASE_URL="",
        )
        self.assertTrue(config.cache_enabled)
        self.assertFalse(config.redis_cache_enabled)
        self.assertFalse(config.qdrant_cache_enabled)
        self.assertFalse(config.inference_cache_enabled)
        self.assertFalse(config.shadow_cache_enabled)

    def test_explicit_service_toggles_override_urls_and_layer_flags(self):
        self._load_env(
            CACHE_MODE="active",
            REDIS_ENABLED="false",
            USE_REDIS_EXACT_MATCHING="true",
            QDRANT_ENABLED="false",
            USE_QDRANT_EXACT_MATCHING="true",
            INFERENCE_ENABLED="false",
            INFERENCE_BASE_URL="http://localhost:7997",
        )
        self.assertFalse(config.redis_cache_enabled)
        self.assertFalse(config.qdrant_cache_enabled)
        self.assertFalse(config.inference_cache_enabled)
        self.assertFalse(config.cache_layer_enabled("redis"))
        self.assertFalse(config.cache_layer_enabled("qdrant_exact"))

    async def test_initialize_shadow_with_all_services_off_skips_external_init(self):
        self._load_env(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="false",
            USE_QDRANT_EXACT_MATCHING="false",
            USE_QDRANT_SEMANTIC_MATCHING="false",
            INFERENCE_BASE_URL="",
        )
        with (
            patch.object(
                DependencyContainer, "get_redis_service", new_callable=AsyncMock
            ) as get_redis,
            patch.object(
                DependencyContainer, "get_qdrant_client_service"
            ) as get_qdrant,
            patch.object(
                DependencyContainer, "get_llm_cache_collection_service"
            ) as get_cache_col,
            patch.object(DependencyContainer, "get_embedding_service") as get_embed,
            patch.object(DependencyContainer, "get_inference_client") as get_inf,
            patch.object(DependencyContainer, "get_shadow_cache_service") as get_shadow,
            patch.object(DependencyContainer, "get_judge_service") as get_judge,
        ):
            fake_redis = MagicMock()
            get_redis.return_value = fake_redis
            DependencyContainer._redis_service = fake_redis

            await DependencyContainer.initialize()

            get_redis.assert_awaited_once_with(ping=False)
            get_qdrant.assert_not_called()
            get_cache_col.assert_not_called()
            get_embed.assert_not_called()
            get_inf.assert_not_called()
            get_shadow.assert_not_called()
            get_judge.assert_not_called()

            router = await DependencyContainer.get_llm_router_service()
            self.assertIsNone(router.redis_service)
            self.assertIsNone(router.llm_cache_service)
            self.assertIsNone(router.embedding_service)
            self.assertIsNone(router.shadow_cache_service)
            self.assertIsNone(router.judge_service)

    async def test_initialize_step_1_redis_only(self):
        self._load_env(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="true",
            USE_QDRANT_EXACT_MATCHING="false",
            USE_QDRANT_SEMANTIC_MATCHING="false",
            INFERENCE_BASE_URL="",
            SHADOW_JUDGE_ENABLED="false",
        )
        with (
            patch.object(
                DependencyContainer, "get_redis_service", new_callable=AsyncMock
            ) as get_redis,
            patch.object(
                DependencyContainer, "get_qdrant_client_service"
            ) as get_qdrant,
            patch.object(DependencyContainer, "get_embedding_service") as get_embed,
            patch.object(DependencyContainer, "get_inference_client") as get_inf,
        ):
            fake_redis = MagicMock()
            get_redis.return_value = fake_redis
            DependencyContainer._redis_service = fake_redis

            await DependencyContainer.initialize()

            self.assertEqual(get_redis.await_args_list[0].kwargs, {"ping": True})
            get_qdrant.assert_not_called()
            get_embed.assert_not_called()
            get_inf.assert_not_called()

            router = await DependencyContainer.get_llm_router_service()
            self.assertIs(router.redis_service, fake_redis)
            self.assertIsNone(router.llm_cache_service)
            self.assertIsNone(router.embedding_service)
            self.assertIsNotNone(router.shadow_cache_service)

    async def test_initialize_step_2_qdrant_without_inference(self):
        self._load_env(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="false",
            USE_QDRANT_EXACT_MATCHING="true",
            USE_QDRANT_SEMANTIC_MATCHING="false",
            INFERENCE_BASE_URL="",
            SHADOW_JUDGE_ENABLED="false",
        )
        fake_cache_col = MagicMock()
        fake_cache_col.ensure_indexes_async = AsyncMock()
        with (
            patch.object(
                DependencyContainer, "get_redis_service", new_callable=AsyncMock
            ) as get_redis,
            patch.object(
                DependencyContainer, "get_qdrant_client_service"
            ) as get_qdrant,
            patch.object(
                DependencyContainer,
                "get_llm_cache_collection_service",
                return_value=fake_cache_col,
            ),
            patch.object(DependencyContainer, "get_embedding_service") as get_embed,
            patch.object(DependencyContainer, "get_inference_client") as get_inf,
        ):
            fake_redis = MagicMock()
            get_redis.return_value = fake_redis
            DependencyContainer._redis_service = fake_redis

            await DependencyContainer.initialize()

            get_redis.assert_awaited_once_with(ping=False)
            get_qdrant.assert_called_once()
            fake_cache_col.ensure_indexes_async.assert_awaited_once()
            get_embed.assert_not_called()
            get_inf.assert_not_called()

            router = await DependencyContainer.get_llm_router_service()
            self.assertIsNone(router.redis_service)
            self.assertIs(router.llm_cache_service, fake_cache_col)
            self.assertIsNone(router.embedding_service)

    async def test_qdrant_collection_service_does_not_require_inference(self):
        self._load_env(
            CACHE_MODE="active",
            USE_QDRANT_EXACT_MATCHING="true",
            INFERENCE_BASE_URL="",
            INFERENCE_EMBEDDING_DIM="512",
        )
        with (
            patch("app.core.dependencies.QdrantClientService") as qdrant_cls,
            patch("app.core.dependencies.LlmCacheCollectionService") as col_cls,
        ):
            DependencyContainer.get_llm_cache_collection_service()
            col_cls.assert_called_once_with(
                client_service=qdrant_cls.return_value,
                collection_name=config.QDRANT_COLLECTION_NAME,
                vector_size=config.INFERENCE_EMBEDDING_DIM,
            )

    async def test_redis_service_with_empty_url_is_fail_open_noop(self):
        svc = RedisService(redis_url="")
        self.assertIsNone(svc.redis)
        await svc.ping()
        self.assertIsNone(await svc.get("k"))
        self.assertIsNone(await svc.set("k", "v"))
        self.assertIsNone(await svc.incr_with_ttl("k", 60))
        await svc.close()

    async def test_router_qdrant_exact_without_embedding_or_intent_services(self):
        fake_cache = MagicMock()
        fake_cache.upsert = MagicMock(return_value="pt-1")
        router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=None,
            llm_cache_service=fake_cache,
            redis_service=None,
            reranker_service=None,
            intent_classifier_service=None,
            entity_extractor_service=None,
            llm_usage_service=MagicMock(),
        )
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )
        semantic_res = await router.evaluate_semantic_cache(req, force=True)
        self.assertIsNone(semantic_res["response"])
        self.assertEqual(semantic_res["matches"], [])

        from app.modules.chat.chat_schema import Choice, Usage

        resp = MagicMock()
        resp.choices = [
            Choice(index=0, message=ChatMessage(role="assistant", content="hi"))
        ]
        resp.model = "gpt-4o-mini"
        resp.usage = Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7)
        resp.cache_info = {}
        resp.model_dump = MagicMock(return_value={"choices": []})
        resp.system_fingerprint = None

        point_id, ttl = await router.save_to_qdrant_cache(
            request=req,
            response=resp,
            save_to_qdrant=True,
        )
        self.assertEqual(point_id, "pt-1")
        self.assertEqual(ttl, config.DEFAULT_CACHE_TTL_SECONDS)
        self.assertEqual(
            fake_cache.upsert.call_args.kwargs["scope"], CacheScope.GLOBAL.value
        )

    def test_health_deps_reports_disabled_services_without_degrading(self):
        self._load_env(
            CACHE_MODE="shadow",
            USE_REDIS_EXACT_MATCHING="false",
            USE_QDRANT_EXACT_MATCHING="false",
            USE_QDRANT_SEMANTIC_MATCHING="false",
            INFERENCE_BASE_URL="",
        )
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_celery_broker] = lambda: True
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/health/deps")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "healthy")
            self.assertEqual(data["failed"], [])
            self.assertEqual(data["checks"]["redis"], "disabled")
            self.assertEqual(data["checks"]["vector_db"], "disabled")
            self.assertEqual(data["checks"]["inference"], "disabled")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
