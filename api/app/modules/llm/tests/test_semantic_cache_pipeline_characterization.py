"""Characterization tests for evaluate_semantic_cache pipeline stage outputs.

Pins the behavior of the tenant/model-scoped semantic cache pipeline on fixture
sets to prevent regressions during refactoring. These tests characterize outputs
(stage by stage) rather than asserting implementation details.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.core import bulkhead as bulkhead_mod
from app.core.circuit_breaker import circuit_breaker_registry
from app.core.config import config
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    FunctionDefinition,
    Tool,
)
from app.modules.llm.llm_router_service import LlmRouterService


def _request(
    model="gpt-4o-mini", service="openai", user_content="What is 2+2?"
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name=service,
        model=model,
        messages=[ChatMessage(role="user", content=user_content)],
        tenant_id="tenant-a",
        user_id="user-1",
    )


def _response(model="gpt-4o-mini") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1,
        model=model,
        choices=[Choice(index=0, message=ChatMessage(role="assistant", content="4"))],
        usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    )


def _router(
    cache_service=None, redis_service=None, embedding_service=None
) -> LlmRouterService:
    if cache_service is None:
        cache_service = MagicMock()
    if redis_service is None:
        redis_service = AsyncMock()
    if embedding_service is None:
        embedding_service = MagicMock()
        embedding_service.model_name = "BAAI/bge-small-en-v1.5"
        embedding_service.get_embedding = AsyncMock(return_value=[0.1] * 384)
        embedding_service.get_sparse_embedding = AsyncMock(
            return_value={"indices": [1, 2], "values": [0.5, 0.3]}
        )
    return LlmRouterService(
        llm_provider_service=MagicMock(),
        embedding_service=embedding_service,
        llm_cache_service=cache_service,
        redis_service=redis_service,
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
        llm_quota_service=None,
        gateway_requests_service=MagicMock(),
    )


class TestSemanticCachePipelineCharacterization(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()
        self._saved = {
            name: getattr(config, name)
            for name in (
                "USE_FALLBACK_LLM",
                "USE_REDIS_EXACT_MATCHING",
                "USE_QDRANT_EXACT_MATCHING",
                "USE_QDRANT_SEMANTIC_MATCHING",
                "CACHE_MODE",
                "LLM_MAX_RETRIES",
                "QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD",
            )
        }
        config.USE_FALLBACK_LLM = "false"
        config.USE_REDIS_EXACT_MATCHING = "true"
        config.USE_QDRANT_EXACT_MATCHING = "true"
        config.USE_QDRANT_SEMANTIC_MATCHING = "true"
        config.CACHE_MODE = "active"
        config.LLM_MAX_RETRIES = 0

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        circuit_breaker_registry.reset_all()
        bulkhead_mod.bulkhead_manager.reset()

    async def test_pipeline_stage_structure_on_miss(self):
        """Characterize: when no cache hits, pipeline returns miss structure."""
        cache_service = MagicMock()
        cache_service.search_exact = MagicMock(return_value=(None, None))
        cache_service.search = MagicMock(return_value=[])
        router = _router(cache_service=cache_service)

        req = _request()
        result = await router.evaluate_semantic_cache(req)

        # Characterize stage outputs
        self.assertIsInstance(result, dict)
        self.assertIn("response", result)
        self.assertIn("cache_info", result)
        self.assertIn("miss_cache_info", result)
        self.assertIn("intent", result)
        self.assertIn("time_sensitivity", result)
        self.assertIn("matches", result)
        self.assertIn("eligible_matches", result)
        self.assertIn("latency_breakdown", result)
        self.assertIn("embeddings", result)
        self.assertIsNone(result["response"])
        self.assertIsInstance(result["matches"], list)
        self.assertIsInstance(result["eligible_matches"], list)
        self.assertIsInstance(result["latency_breakdown"], dict)

    async def test_pipeline_bypasses_with_tools(self):
        """Characterize: requests with tools bypass semantic cache."""
        cache_service = MagicMock()
        router = _router(cache_service=cache_service)
        req = _request()
        # Assign a validated ``Tool`` (not a raw dict) so the request matches
        # what the API layer produces after Pydantic validation.
        req.tools = [
            Tool(
                type="function",
                function=FunctionDefinition(name="test", parameters={}),
            )
        ]

        result = await router.evaluate_semantic_cache(req)
        self.assertIsNone(result["response"])
        self.assertIsNone(result["cache_info"])
        self.assertEqual(result["matches"], [])

    async def test_pipeline_disabled_semantic_matching_returns_empty(self):
        """Characterize: when semantic matching disabled, pipeline short-circuits."""
        config.USE_QDRANT_SEMANTIC_MATCHING = "false"
        cache_service = MagicMock()
        router = _router(cache_service=cache_service)
        req = _request()

        result = await router.evaluate_semantic_cache(req)
        self.assertIsNone(result["response"])
        self.assertEqual(result["matches"], [])
        self.assertIn("latency_breakdown", result)

    async def test_tenant_scoped_exact_hash_generation(self):
        """Characterize: tenant affects cache key (tenant/model-scoped correctness)."""
        router = _router()
        req1 = _request(model="gpt-4o-mini")
        req1.tenant_id = "tenant-a"
        req2 = _request(model="gpt-4o-mini")
        req2.tenant_id = "tenant-b"

        hash1 = router._generate_cache_key(req1)
        hash2 = router._generate_cache_key(req2)
        self.assertIsInstance(hash1, str)
        self.assertIsInstance(hash2, str)
        self.assertEqual(len(hash1), 64)  # SHA-256 hex
        self.assertNotEqual(
            hash1, hash2, "Different tenants must produce different cache keys"
        )

    async def test_model_scoped_exact_hash_generation(self):
        """Characterize: model affects cache key (tenant/model-scoped correctness)."""
        router = _router()
        req1 = _request(model="openai/gpt-oss-20b", service="groq")
        req2 = _request(model="openai/gpt-oss-safeguard-20b", service="groq")
        hash1 = router._generate_cache_key(req1)
        hash2 = router._generate_cache_key(req2)
        self.assertNotEqual(
            hash1, hash2, "Different models must produce different cache keys"
        )

    async def test_pipeline_latency_breakdown_keys_present_on_miss(self):
        """Characterize latency breakdown structure."""
        cache_service = MagicMock()
        cache_service.search_exact = MagicMock(return_value=(None, None))
        cache_service.search = MagicMock(return_value=[])
        router = _router(cache_service=cache_service)
        req = _request()
        result = await router.evaluate_semantic_cache(req)
        breakdown = result["latency_breakdown"]
        self.assertIsInstance(breakdown, dict)
        # Common breakdown keys may be present
        for key in ["cache_lookup_total_ms"]:
            if key in breakdown:
                self.assertIsInstance(breakdown[key], (int, float))

    async def test_prepare_prompts_characterization(self):
        """Characterize prompt preparation for cache keying."""
        router = _router()
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="You are helpful"),
                ChatMessage(role="user", content="Hello world"),
            ],
        )
        prompt_text, system_prompt, user_prompt = router._prepare_prompts(req)
        self.assertIsInstance(prompt_text, str)
        self.assertIsInstance(system_prompt, str)
        self.assertIsInstance(user_prompt, str)
        self.assertIn("Hello world", user_prompt)
        self.assertIn("You are helpful", system_prompt)


if __name__ == "__main__":
    unittest.main()
