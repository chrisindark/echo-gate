import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import config
from app.core.constants import MOCK_RESPONSE_TEXT
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    ToolCallChunk,
)
from app.modules.llm import prompts
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.llm_router_service import (
    LlmRouterService,
    _chunk_has_content,
    is_mock_response,
)


class TestRouterCoverageBoost(unittest.IsolatedAsyncioTestCase):
    def test_prompts_imported(self):
        self.assertTrue(len(prompts.judge_prompt) > 0)
        self.assertTrue(len(prompts.system_prompt_summary) > 0)
        self.assertTrue(len(prompts.user_prompt_summary) > 0)

    def test_is_mock_response(self):
        self.assertFalse(is_mock_response(None))

        resp_empty = ChatCompletionResponse(
            id="1", object="chat.completion", created=1, model="m", choices=[]
        )
        self.assertFalse(is_mock_response(resp_empty))

        resp_fp = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            system_fingerprint="mock",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="test"))
            ],
        )
        self.assertTrue(is_mock_response(resp_fp))

        resp_text = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=MOCK_RESPONSE_TEXT),
                )
            ],
        )
        self.assertTrue(is_mock_response(resp_text))

        resp_normal = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Normal response"),
                )
            ],
        )
        self.assertFalse(is_mock_response(resp_normal))

    def test_chunk_has_content(self):
        c_empty = ChatCompletionChunk(id="1", created=1, model="m", choices=[])
        self.assertFalse(_chunk_has_content(c_empty))

        c_no_delta = ChatCompletionChunk(
            id="1",
            created=1,
            model="m",
            choices=[ChunkChoice(index=0, delta=ChunkDelta())],
        )
        self.assertFalse(_chunk_has_content(c_no_delta))

        c_content = ChatCompletionChunk(
            id="1",
            created=1,
            model="m",
            choices=[ChunkChoice(index=0, delta=ChunkDelta(content="hi"))],
        )
        self.assertTrue(_chunk_has_content(c_content))

        c_reasoning = ChatCompletionChunk(
            id="1",
            created=1,
            model="m",
            choices=[
                ChunkChoice(index=0, delta=ChunkDelta(reasoning_content="thinking"))
            ],
        )
        self.assertTrue(_chunk_has_content(c_reasoning))

        c_tools = ChatCompletionChunk(
            id="1",
            created=1,
            model="m",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(
                        tool_calls=[ToolCallChunk(index=0, id="1", type="function")]
                    ),
                )
            ],
        )
        self.assertTrue(_chunk_has_content(c_tools))

    async def test_router_cache_hit_and_shutdown(self):
        mock_provider = MagicMock(spec=LlmProviderService)
        mock_cache = MagicMock()

        router = LlmRouterService(
            llm_provider_service=mock_provider,
            embedding_service=MagicMock(),
            llm_cache_service=mock_cache,
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        # clear_embedding_cache
        router._embedding_cache["test"] = ([1.0], None)
        self.assertEqual(len(router._embedding_cache), 1)
        router.clear_embedding_cache()
        self.assertEqual(len(router._embedding_cache), 0)

        # _record_cache_hit
        router._record_cache_hit(None)
        mock_cache.record_hit.assert_not_called()

        resp_no_hit = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="hi"))
            ],
            cache_info={"cache_hit": False},
        )
        router._record_cache_hit(resp_no_hit)
        mock_cache.record_hit.assert_not_called()

        resp_hit = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="m",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="hi"))
            ],
            cache_info={"cache_hit": True, "point_id": "point-123"},
        )
        with patch.object(config, "CACHE_RETENTION_ENABLED", True):
            router._record_cache_hit(resp_hit)
            # Give background task event loop a turn
            await asyncio.sleep(0.05)

        # shutdown with background tasks
        async def dummy_bg():
            await asyncio.sleep(0.01)

        router._create_background_task(dummy_bg())
        self.assertGreaterEqual(len(router._background_tasks), 1)
        await router.shutdown()
        self.assertEqual(len(router._background_tasks), 0)

    async def test_call_provider_with_retry_providers(self):
        mock_provider = MagicMock(spec=LlmProviderService)
        dummy_resp = ChatCompletionResponse(
            id="1",
            object="chat.completion",
            created=1,
            model="gpt-4o-mini",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="answer"))
            ],
        )
        mock_provider.generate_groq_completion = AsyncMock(return_value=dummy_resp)
        mock_provider.generate_openrouter_completion = AsyncMock(
            return_value=dummy_resp
        )
        mock_provider.generate_ollama_completion = AsyncMock(return_value=dummy_resp)

        router = LlmRouterService(
            llm_provider_service=mock_provider,
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        req = ChatCompletionRequest(
            service_name="groq",
            model="openai/gpt-oss-20b",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # Groq
        resp, svc, _ = await router._call_provider_with_retry(
            request=req, service_name="groq", timeout_seconds=5.0, max_retries=0
        )
        self.assertEqual(svc, "groq")
        mock_provider.generate_groq_completion.assert_awaited_once()

        # OpenRouter
        req_or = ChatCompletionRequest(
            service_name="openrouter",
            model="google/gemma-4-26b-a4b-it:free",
            messages=[ChatMessage(role="user", content="hello")],
        )
        resp, svc, _ = await router._call_provider_with_retry(
            request=req_or,
            service_name="openrouter",
            timeout_seconds=5.0,
            max_retries=0,
        )
        self.assertEqual(svc, "openrouter")
        mock_provider.generate_openrouter_completion.assert_awaited_once()

        # Ollama
        req_ol = ChatCompletionRequest(
            service_name="ollama",
            model="qwen2.5-coder:3b",
            messages=[ChatMessage(role="user", content="hello")],
        )
        resp, svc, _ = await router._call_provider_with_retry(
            request=req_ol, service_name="ollama", timeout_seconds=5.0, max_retries=0
        )
        self.assertEqual(svc, "ollama")
        mock_provider.generate_ollama_completion.assert_awaited_once()

    async def test_call_provider_timeout_budget_and_client_errors(self):
        from fastapi import HTTPException

        mock_provider = MagicMock(spec=LlmProviderService)
        router = LlmRouterService(
            llm_provider_service=mock_provider,
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # Timeout budget exceeded raises 504
        with self.assertRaises(HTTPException) as ctx:
            await router._call_provider_with_retry(
                request=req,
                service_name="openai",
                timeout_seconds=5.0,
                timeout_budget_ms=1.0,
            )
        self.assertEqual(ctx.exception.status_code, 504)

        # Client error 401 raises immediately
        mock_provider.generate_openai_completion = AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Invalid API Key")
        )
        with self.assertRaises(HTTPException) as ctx2:
            await router._call_provider_with_retry(
                request=req, service_name="openai", timeout_seconds=5.0
            )
        self.assertEqual(ctx2.exception.status_code, 401)

    async def test_evaluate_semantic_cache_filters(self):
        mock_cache = MagicMock()
        mock_cache.query_points_dense.return_value = []
        mock_cache.query_points_rrf.return_value = []
        mock_embed = MagicMock()
        mock_embed.model_name = "test-embed"
        mock_embed.get_embedding_async = AsyncMock(return_value=[0.1] * 768)
        mock_embed.get_sparse_embedding = MagicMock(return_value=None)

        router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=mock_embed,
            llm_cache_service=mock_cache,
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        req_filters = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System inst"),
                ChatMessage(role="user", content="User prompt"),
            ],
            response_format={"type": "json_object"},
            stop=["STOP"],
            max_tokens=256,
            tenant_id="ten-1",
            user_id="usr-1",
            session_id="ses-1",
            conversation_id="cnv-1",
        )

        res = await router.evaluate_semantic_cache(req_filters, force=True)
        self.assertIsNotNone(res["latency_breakdown"])
        mock_cache.query_points_rrf.assert_called_once()
        q_filter = mock_cache.query_points_rrf.call_args[1].get("query_filter")
        self.assertIsNotNone(q_filter)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
