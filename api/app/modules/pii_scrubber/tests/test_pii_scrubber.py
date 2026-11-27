"""Unit and integration tests for Stage 0 Pre-Routing PII Scrubber."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.database import get_db
from app.core.dependencies import get_llm_router_service
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    FunctionCall,
    ToolCall,
    Usage,
    apply_request_directives,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm.providers.common import prepare_openai_compatible_payload
from app.modules.pii_scrubber.pii_scrubber_service import (
    PiiScrubberService,
    is_valid_iban,
    is_valid_luhn,
)
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


def _ok_response(text: str = "Masked OK") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-pii-1",
        created=123456789,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=text),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        cache_info={"matched": False, "cache_hit": False},
    )


class TestPiiScrubberValidators(unittest.TestCase):
    def setUp(self):
        self.scrubber = PiiScrubberService()

    def test_luhn_and_iban_validators(self):
        # Standard Visa / Mastercard test numbers (valid Luhn) vs invalid checksum
        self.assertTrue(is_valid_luhn("4111-1111-1111-1111"))
        self.assertTrue(is_valid_luhn("5500 0000 0000 0004"))
        self.assertFalse(is_valid_luhn("4111-1111-1111-1112"))
        self.assertFalse(is_valid_luhn("0000000000000000"))

        # Valid DE / GB IBANs vs invalid check digits
        self.assertTrue(is_valid_iban("DE89 3704 0044 0532 0130 00"))
        self.assertTrue(is_valid_iban("GB82WEST12345698765432"))
        self.assertFalse(is_valid_iban("DE00 3704 0044 0532 0130 00"))

    def test_scrub_text_masks_all_five_pii_types_without_false_positives(self):
        sample = (
            "Contact alice.smith@example.com or +1 (415) 555-0199. "
            "Card 4111-1111-1111-1111, SSN 123-45-6789, "
            "IBAN DE89 3704 0044 0532 0130 00. "
            "Keep date 2026-11-26, version 1.2.3, IP 192.168.1.1, and invalid card 4111-1111-1111-1112."
        )
        masked, counts = self.scrubber.scrub_text(sample)

        self.assertEqual(
            counts,
            {"email": 1, "credit_card": 1, "ssn": 1, "iban": 1, "phone": 1},
        )
        self.assertIn("[EMAIL]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertIn("[CREDIT_CARD]", masked)
        self.assertIn("[SSN]", masked)
        self.assertIn("[IBAN]", masked)
        self.assertNotIn("alice.smith@example.com", masked)
        self.assertNotIn("415", masked)
        self.assertNotIn("4111-1111-1111-1111", masked)
        self.assertNotIn("123-45-6789", masked)
        self.assertNotIn("DE89", masked)

        # Non-PII tokens and invalid checksum numbers remain untouched
        self.assertIn("2026-11-26", masked)
        self.assertIn("1.2.3", masked)
        self.assertIn("192.168.1.1", masked)
        self.assertIn("4111-1111-1111-1112", masked)

    def test_scrub_request_preserves_private_attrs_multimodal_and_tool_calls(self):
        msg = ChatMessage(
            role="user",
            content="Email bob@acme.org",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    function=FunctionCall(
                        name="lookup",
                        arguments='{"ssn": "123-45-6789"}',
                    ),
                )
            ],
        )
        msg.content_parts = [{"type": "text", "text": "Phone 555-234-5678"}]
        msg.cache_control = {"type": "ephemeral"}

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[msg],
            redact_pii=True,
        )
        req.tenant_id = "t-1"
        req.user_id = "u-1"
        req.allowed_providers = frozenset({"openai"})

        scrubbed, res = self.scrubber.scrub_request(req)
        self.assertTrue(res.applied)
        self.assertEqual(res.entity_counts, {"email": 1, "phone": 1, "ssn": 1})
        self.assertEqual(scrubbed.messages[0].content, "Email [EMAIL]")
        self.assertEqual(
            scrubbed.messages[0].content_parts,
            [{"type": "text", "text": "Phone [PHONE]"}],
        )
        self.assertEqual(
            scrubbed.messages[0].tool_calls[0].function.arguments,
            '{"ssn": "[SSN]"}',
        )
        self.assertEqual(scrubbed.messages[0].cache_control, {"type": "ephemeral"})
        self.assertEqual(scrubbed.tenant_id, "t-1")
        self.assertEqual(scrubbed.user_id, "u-1")
        self.assertEqual(scrubbed.allowed_providers, frozenset({"openai"}))


class TestPiiScrubberPreRoutingIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.usage_svc = MagicMock()
        self.usage_svc.create_llm_usage_log = MagicMock()
        self.gateway_svc = MagicMock()
        self.gateway_svc.log_request = MagicMock()
        self.cache_svc = MagicMock()
        self.cache_svc.search_exact = MagicMock(return_value=(None, None))
        self.cache_svc.upsert = MagicMock(return_value="pt-pii")
        self.redis_svc = MagicMock()
        self.redis_svc.get = AsyncMock(return_value=None)
        self.redis_svc.set = AsyncMock()
        self.embedding_svc = MagicMock()
        self.embedding_svc.model_name = "BAAI/bge-small-en-v1.5"
        self.embedding_svc.get_embedding_async = AsyncMock(return_value=[0.1, 0.2])
        self.embedding_svc.get_sparse_embedding = MagicMock(return_value=None)

        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=self.embedding_svc,
            llm_cache_service=self.cache_svc,
            redis_service=self.redis_svc,
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=self.usage_svc,
            gateway_requests_service=self.gateway_svc,
            prompt_optimizer=PromptOptimizer(),
            pii_scrubber=PiiScrubberService(),
        )

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[api_key_auth_optional_guard] = lambda: None
        app.dependency_overrides[llm_quota_guard] = lambda: None
        self.client = TestClient(app)

        self._saved_pii = config.PII_SCRUBBING_ENABLED
        self._saved_cache_mode = config.CACHE_MODE
        self._saved_guardrails_enabled = config.GUARDRAILS_ENABLED
        config.GUARDRAILS_ENABLED = False

    def tearDown(self):
        app.dependency_overrides.clear()
        config.PII_SCRUBBING_ENABLED = self._saved_pii
        config.CACHE_MODE = self._saved_cache_mode
        config.GUARDRAILS_ENABLED = self._saved_guardrails_enabled

    def test_directive_and_payload_stripping(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="test")],
        )
        apply_request_directives(req, {EchoGateHeaders.REDACT_PII: "true"})
        self.assertTrue(req.redact_pii)
        self.assertNotIn("redact_pii", prepare_openai_compatible_payload(req))

    def test_pii_scrubbed_before_provider_cache_key_and_logs(self):
        config.CACHE_MODE = "active"
        captured_requests: list[ChatCompletionRequest] = []

        async def _fake_provider(request, **kwargs):
            captured_requests.append(request)
            return _ok_response(), "openai", "gpt-4o-mini"

        self.router._lookup_serving_cache = AsyncMock(return_value=(None, {}))
        self.router._call_provider_with_retry = AsyncMock(side_effect=_fake_provider)
        self.router._background_save_cache = AsyncMock()

        raw_email = "john.doe@secret.org"
        raw_ssn = "123-45-6789"
        resp = self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Email {raw_email} SSN {raw_ssn}",
                    }
                ],
            },
            headers={EchoGateHeaders.REDACT_PII: "true"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get(EchoGateHeaders.PII_REDACTED), "email=1,ssn=1"
        )

        # Provider received masked text only.
        self.assertEqual(len(captured_requests), 1)
        sent_text = captured_requests[0].messages[0].content
        self.assertEqual(sent_text, "Email [EMAIL] SSN [SSN]")

        # Cache lookup & cache save received masked text only, and cache key
        # matches a pre-masked request.
        lookup_req = self.router._lookup_serving_cache.call_args.kwargs["request"]
        self.assertEqual(lookup_req.messages[0].content, "Email [EMAIL] SSN [SSN]")
        save_req = self.router._background_save_cache.call_args.kwargs["request"]
        self.assertEqual(save_req.messages[0].content, "Email [EMAIL] SSN [SSN]")
        expected_req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Email [EMAIL] SSN [SSN]")],
        )
        expected_req.tenant_id = lookup_req.tenant_id
        expected_req.user_id = lookup_req.user_id
        expected_hash = self.router._generate_cache_key(expected_req)

        raw_req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(
                    role="user",
                    content=f"Email {raw_email} SSN {raw_ssn}",
                )
            ],
        )
        raw_req.tenant_id = lookup_req.tenant_id
        raw_req.user_id = lookup_req.user_id
        raw_hash = self.router._generate_cache_key(raw_req)

        actual_hash = self.router._lookup_serving_cache.call_args.kwargs["exact_hash"]
        self.assertEqual(actual_hash, expected_hash)
        self.assertNotEqual(actual_hash, raw_hash)

        # Usage and gateway logs contain zero raw PII.
        usage_entry = self.usage_svc.create_llm_usage_log.call_args[0][0]
        self.assertNotIn(raw_email, usage_entry.input_text)
        self.assertNotIn(raw_ssn, usage_entry.input_text)
        gw_entry = self.gateway_svc.log_request.call_args[0][0]
        self.assertNotIn(raw_email, gw_entry.query_text)
        self.assertNotIn(raw_ssn, gw_entry.query_text)

    def test_anthropic_messages_pii_scrubbing(self):
        captured_requests: list[ChatCompletionRequest] = []

        async def _fake_provider(request, **kwargs):
            captured_requests.append(request)
            return (
                _ok_response("Anthropic clean"),
                "claude",
                "claude-3-5-sonnet-20241022",
            )

        self.router._call_provider_with_retry = AsyncMock(side_effect=_fake_provider)
        self.router._background_save_cache = AsyncMock()

        resp = self.client.post(
            "/api/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 128,
                "messages": [
                    {"role": "user", "content": "IBAN GB82WEST12345698765432"}
                ],
                "redact_pii": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.PII_REDACTED), "iban=1")
        self.assertEqual(captured_requests[0].messages[0].content, "IBAN [IBAN]")

    def test_conversation_turn_scrubs_persisted_user_message(self):
        self.router._call_provider_with_retry = AsyncMock(
            return_value=(_ok_response("Conv reply"), "openai", "gpt-4o-mini")
        )
        self.router._background_save_cache = AsyncMock()

        fake_conv = SimpleNamespace(id="conv-pii-1", user_id="anonymous")
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
                "/api/v1/conversations/conv-pii-1/chat",
                json={
                    "service_name": "openai",
                    "model": "gpt-4o-mini",
                    "message": "Call me at (415) 555-0142",
                    "redact_pii": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get(EchoGateHeaders.PII_REDACTED), "phone=1")
        fake_conv_svc.record_turn.assert_called_once()
        self.assertEqual(
            fake_conv_svc.record_turn.call_args.kwargs["user_content"],
            "Call me at [PHONE]",
        )


if __name__ == "__main__":
    unittest.main()
