"""Integration tests for Zero-Retention Mode (`disable_log` / `X-EchoGate-Disable-Log`).

Covers:
- `disable_log: true` in request body and `X-EchoGate-Disable-Log: true` header.
- Precedence over `cache_enabled: true` (always forces `X-Cache: BYPASS`).
- Quota enforcement (`llm_quota_guard` + Redis `record_tokens`) preserved.
- Complete bypass of PostgreSQL (`gateway_requests_log`, `llm_usage_log`,
  `shadow_cache_evaluations`, `cache_evaluations`, `conversation_messages`) and
  Redis/Qdrant cache reads and writes in both `active` and `shadow` modes.
- OpenAI `/api/v1/chat/completions`, Anthropic `/api/v1/messages` & `/v1/messages`,
  and Conversations `/api/v1/conversations/{id}/chat` (streaming & non-streaming).
- Stripping `disable_log` from OpenAI-compatible upstream payloads.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient

from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.database import get_db
from app.core.dependencies import get_llm_router_service
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    Usage,
    apply_request_directives,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm.providers.common import prepare_openai_compatible_payload
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


def _mock_response(text: str = "Zero-retention reply") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-zr-1",
        created=123456789,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=text),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        cache_info={"matched": False, "cache_hit": False},
    )


class TestZeroRetentionMode(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.usage_svc = MagicMock()
        self.usage_svc.create_llm_usage_log = MagicMock()
        self.gateway_svc = MagicMock()
        self.gateway_svc.log_request = MagicMock()
        self.quota_svc = MagicMock()
        self.quota_svc.record_tokens = AsyncMock()
        self.shadow_svc = MagicMock()
        self.shadow_svc.log_evaluation = MagicMock()
        self.audit_svc = MagicMock()
        self.audit_svc.log_audit = MagicMock()
        self.redis_svc = MagicMock()
        self.redis_svc.get = AsyncMock(return_value=None)
        self.redis_svc.set = AsyncMock()
        self.cache_svc = MagicMock()
        self.cache_svc.search_exact = MagicMock(return_value=(None, None))
        self.cache_svc.upsert = MagicMock()

        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=self.cache_svc,
            redis_service=self.redis_svc,
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=self.usage_svc,
            llm_quota_service=self.quota_svc,
            gateway_requests_service=self.gateway_svc,
            shadow_cache_service=self.shadow_svc,
            judge_service=MagicMock(),
            cache_audit_service=self.audit_svc,
            prompt_optimizer=PromptOptimizer(),
        )

        self.quota_guard_calls = 0

        async def _quota_guard(request: Request):
            self.quota_guard_calls += 1
            if request.headers.get(EchoGateHeaders.API_KEY):
                request.state.api_key = "eg-resolved-key"
            return None

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[api_key_auth_optional_guard] = lambda: None
        app.dependency_overrides[llm_quota_guard] = _quota_guard
        self.client = TestClient(app)

        self._saved_cache_mode = config.CACHE_MODE

    def tearDown(self):
        app.dependency_overrides.clear()
        config.CACHE_MODE = self._saved_cache_mode

    def test_directive_and_provider_payload_stripping(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="secret medical record")],
            cache_enabled=True,
        )
        self.assertFalse(req.cache_bypassed)

        apply_request_directives(req, {EchoGateHeaders.DISABLE_LOG: "true"})
        self.assertTrue(req.disable_log)
        # Zero-retention must always win over cache_enabled=True.
        self.assertTrue(req.cache_bypassed)

        # Header "false" must not downgrade an explicit body disable_log=True.
        apply_request_directives(req, {EchoGateHeaders.DISABLE_LOG: "false"})
        self.assertTrue(req.disable_log)

        upstream_payload = prepare_openai_compatible_payload(req)
        self.assertNotIn("disable_log", upstream_payload)

    def test_chat_completions_zero_retention_skips_db_and_cache_keeps_quota(self):
        config.CACHE_MODE = "active"
        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(_mock_response(), "openai", "gpt-4o-mini")
        )
        self.router._background_save_cache = AsyncMock()

        resp = self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Patient SSN 123-45-6789"}],
                "disable_log": True,
                "cache_enabled": True,
            },
            headers={EchoGateHeaders.API_KEY: "eg-test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
        self.assertEqual(self.quota_guard_calls, 1)

        # Quota TPM tracking still runs; DB/cache persistence never runs.
        self.quota_svc.record_tokens.assert_awaited_once()
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.router._lookup_serving_cache.assert_not_called()
        self.router._background_save_cache.assert_not_called()
        self.redis_svc.get.assert_not_called()
        self.redis_svc.set.assert_not_called()
        self.cache_svc.upsert.assert_not_called()

    def test_chat_completions_shadow_mode_with_header_skips_shadow_eval_and_db(self):
        config.CACHE_MODE = "shadow"
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(_mock_response(), "openai", "gpt-4o-mini")
        )
        self.router._background_shadow_evaluate = AsyncMock()

        resp = self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "HIPAA protected prompt"}],
            },
            headers={EchoGateHeaders.DISABLE_LOG: "true"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")

        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.router._background_shadow_evaluate.assert_not_called()
        self.shadow_svc.log_evaluation.assert_not_called()

    def test_chat_completions_streaming_zero_retention(self):
        config.CACHE_MODE = "shadow"

        async def _mock_stream(*args, **kwargs):
            yield (
                ChatCompletionChunk(
                    id="chunk-1",
                    object="chat.completion.chunk",
                    created=123,
                    model="gpt-4o-mini",
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(content="Streamed answer"),
                            finish_reason="stop",
                        )
                    ],
                    usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                ),
                "openai",
                "gpt-4o-mini",
            )

        self.router._lookup_serving_cache = AsyncMock()
        self.router._stream_from_provider_with_fallback = _mock_stream
        self.router._background_save_cache = AsyncMock()
        self.router._background_shadow_evaluate = AsyncMock()

        resp = self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Private stream"}],
                "stream": True,
            },
            headers={
                EchoGateHeaders.DISABLE_LOG: "true",
                EchoGateHeaders.CACHE_PREFER: "instant",
                EchoGateHeaders.API_KEY: "eg-stream-key",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
        self.assertIn("Streamed answer", resp.text)

        self.router._lookup_serving_cache.assert_not_called()
        self.router._background_save_cache.assert_not_called()
        self.router._background_shadow_evaluate.assert_not_called()
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.quota_svc.record_tokens.assert_awaited_once()

    async def test_provider_failure_with_disable_log_skips_error_db_logs(self):
        self.router._call_provider_with_retry = AsyncMock(
            side_effect=RuntimeError("upstream down")
        )
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Sensitive error test")],
            disable_log=True,
        )
        with self.assertRaises(RuntimeError):
            await self.router.generate_completion(req, api_key="eg-key")
        await self.router.shutdown()

        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.quota_svc.record_tokens.assert_not_called()

    def test_anthropic_messages_zero_retention(self):
        config.CACHE_MODE = "active"
        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(
                _mock_response("Claude private reply"),
                "claude",
                "claude-3-5-sonnet-20241022",
            )
        )
        self.router._background_save_cache = AsyncMock()

        resp = self.client.post(
            "/api/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Confidential query"}],
                "disable_log": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
        self.assertEqual(resp.json()["content"][0]["text"], "Claude private reply")

        self.router._lookup_serving_cache.assert_not_called()
        self.router._background_save_cache.assert_not_called()
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()

    def test_conversation_chat_zero_retention_skips_turn_and_db_persistence(self):
        self.router._lookup_serving_cache = AsyncMock()
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(
                _mock_response("Ephemeral chat turn"),
                "openai",
                "gpt-4o-mini",
            )
        )
        self.router._background_save_cache = AsyncMock()

        fake_conv = SimpleNamespace(id="conv-zr-1", user_id="anonymous")
        fake_conv_svc = MagicMock()
        fake_conv_svc.get_conversation = MagicMock(return_value=fake_conv)
        fake_conv_svc.get_conversation_messages = MagicMock(return_value=[])
        fake_conv_svc.record_turn = MagicMock()

        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch(
            "app.modules.conversation.conversation_controller.ConversationService",
            return_value=fake_conv_svc,
        ):
            resp = self.client.post(
                "/api/v1/conversations/conv-zr-1/chat",
                json={
                    "service_name": "openai",
                    "model": "gpt-4o-mini",
                    "message": "Do not store this turn",
                    "disable_log": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")

        fake_conv_svc.record_turn.assert_not_called()
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.router._lookup_serving_cache.assert_not_called()
        self.router._background_save_cache.assert_not_called()

    def test_conversation_streaming_zero_retention_skips_turn_persistence(self):
        async def _mock_stream(*args, **kwargs):
            yield (
                ChatCompletionChunk(
                    id="chunk-conv-1",
                    object="chat.completion.chunk",
                    created=123,
                    model="gpt-4o-mini",
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(content="Private stream turn"),
                            finish_reason="stop",
                        )
                    ],
                ),
                "openai",
                "gpt-4o-mini",
            )

        self.router._stream_from_provider_with_fallback = _mock_stream
        fake_conv = SimpleNamespace(id="conv-zr-2", user_id="anonymous")
        fake_conv_svc = MagicMock()
        fake_conv_svc.get_conversation = MagicMock(return_value=fake_conv)
        fake_conv_svc.get_conversation_messages = MagicMock(return_value=[])

        app.dependency_overrides[get_db] = lambda: MagicMock()
        with (
            patch(
                "app.modules.conversation.conversation_controller.ConversationService",
                return_value=fake_conv_svc,
            ),
            patch(
                "app.modules.conversation.conversation_controller._persist_conversation_turn"
            ) as persist_turn,
        ):
            resp = self.client.post(
                "/api/v1/conversations/conv-zr-2/chat",
                json={
                    "service_name": "openai",
                    "model": "gpt-4o-mini",
                    "message": "Do not store this stream",
                    "stream": True,
                },
                headers={EchoGateHeaders.DISABLE_LOG: "true"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "BYPASS")
        self.assertEqual(resp.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
        self.assertIn("Private stream turn", resp.text)
        persist_turn.assert_not_called()
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
