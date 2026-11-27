"""Unit and integration tests for admin-managed per-tenant privacy & cache policies."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.database import Base
from app.core.dependencies import (
    get_llm_router_service,
    get_tenant_policy_service,
)
from app.core.guards import api_key_auth_optional_guard, llm_quota_guard
from app.core.identity import Identity
from app.main import app
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.guardrails.guardrails_service import GuardrailsService
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.pii_scrubber.pii_scrubber_service import PiiScrubberService
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer
from app.modules.tenant_policy.tenant_policy_schema import TenantPrivacyPolicyUpsert
from app.modules.tenant_policy.tenant_policy_service import (
    TenantPolicyService,
    UnauthenticatedAdminPolicyAccessError,
)


def _dummy_response(text: str = "Policy-enforced response") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-tp-1",
        created=1700000000,
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


class TestTenantPolicyServiceUnit(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.service = TenantPolicyService(
            session_factory=self.Session,
            session_factory_read=self.Session,
        )
        self.admin = AdminKeyIdentity(
            key_id=1, name="ops-admin", scopes=frozenset({"admin:*"})
        )

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_requires_admin_identity(self):
        with self.assertRaises(UnauthenticatedAdminPolicyAccessError):
            self.service.list_policies(admin=None)  # type: ignore[arg-type]

        with self.assertRaises(UnauthenticatedAdminPolicyAccessError):
            self.service.upsert_policy(
                "acme",
                TenantPrivacyPolicyUpsert(),
                admin="not-an-admin",  # type: ignore[arg-type]
            )

    def test_crud_and_runtime_cache_lifecycle(self):
        # Empty initially
        items, total = self.service.list_policies(self.admin)
        self.assertEqual(total, 0)
        self.assertEqual(items, [])
        self.assertIsNone(self.service.get_active_policy("acme"))

        # Upsert create
        created = self.service.upsert_policy(
            "acme",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=False,
                zero_retention=True,
                pii_scrubbing_mode="enforced",
                guardrails_mode="block",
                description="HIPAA org",
                is_active=True,
            ),
            self.admin,
        )
        self.assertEqual(created.tenant_id, "acme")
        self.assertFalse(created.semantic_cache_enabled)
        self.assertTrue(created.zero_retention)
        self.assertEqual(created.pii_scrubbing_mode, "enforced")

        # Hot-path lookup resolves snapshot and caches it
        snap = self.service.get_active_policy("acme")
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertTrue(snap.zero_retention)
        self.assertFalse(snap.semantic_cache_enabled)

        # Update flips to performance mode & invalidates cache
        updated = self.service.upsert_policy(
            "acme",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=True,
                zero_retention=False,
                pii_scrubbing_mode="disabled",
                guardrails_mode="inherit",
                is_active=True,
            ),
            self.admin,
        )
        self.assertTrue(updated.semantic_cache_enabled)
        self.assertFalse(updated.zero_retention)
        self.assertEqual(updated.pii_scrubbing_mode, "disabled")

        snap2 = self.service.get_active_policy("acme")
        self.assertIsNotNone(snap2)
        assert snap2 is not None
        self.assertTrue(snap2.semantic_cache_enabled)
        self.assertFalse(snap2.zero_retention)
        self.assertEqual(snap2.pii_scrubbing_mode, "disabled")

        # Inactive policy is ignored at runtime
        self.service.upsert_policy(
            "acme",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=False,
                zero_retention=True,
                is_active=False,
            ),
            self.admin,
        )
        self.assertIsNone(self.service.get_active_policy("acme"))

        # Filter listing
        active_items, active_total = self.service.list_policies(
            self.admin, is_active=True
        )
        self.assertEqual(active_total, 0)
        all_items, all_total = self.service.list_policies(self.admin, tenant_id="acme")
        self.assertEqual(all_total, 1)

        # Delete
        self.assertTrue(self.service.delete_policy("acme", self.admin))
        self.assertFalse(self.service.delete_policy("acme", self.admin))
        self.assertIsNone(self.service.get_policy("acme", self.admin))

    def test_list_tenants_aggregates_sources(self):
        with self.Session() as s:
            s.add(
                ApiKey(
                    name="k1",
                    key_prefix="eg_1",
                    key_hash="hash1",
                    tenant_id="tenant-keys",
                    is_active=True,
                )
            )
            s.add(LlmQuotaRule(tenant_id="tenant-quota", max_rpm=10, is_active=True))
            s.commit()

        self.service.upsert_policy(
            "tenant-policy", TenantPrivacyPolicyUpsert(), self.admin
        )
        tenants = self.service.list_tenants(self.admin)
        self.assertEqual(tenants, ["tenant-keys", "tenant-policy", "tenant-quota"])

    def test_empty_tenant_rejected_on_upsert(self):
        with self.assertRaises(ValueError):
            self.service.upsert_policy("   ", TenantPrivacyPolicyUpsert(), self.admin)


class TestTenantPolicyAdminApiAndRouterEnforcement(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.policy_service = TenantPolicyService(
            session_factory=self.Session,
            session_factory_read=self.Session,
        )
        self.admin = AdminKeyIdentity(
            key_id=1, name="ops", scopes=frozenset({"admin:*"})
        )

        self.usage_svc = MagicMock()
        self.usage_svc.create_llm_usage_log = MagicMock()
        self.gateway_svc = MagicMock()
        self.gateway_svc.log_request = MagicMock()
        self.quota_svc = MagicMock()
        self.quota_svc.record_tokens = AsyncMock()
        self.shadow_svc = MagicMock()
        self.audit_svc = MagicMock()
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
            pii_scrubber=PiiScrubberService(),
            guardrails_service=GuardrailsService(),
            tenant_policy_service=self.policy_service,
        )

        app.dependency_overrides[get_tenant_policy_service] = lambda: (
            self.policy_service
        )
        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[api_key_auth_optional_guard] = lambda: None
        app.dependency_overrides[llm_quota_guard] = self._mock_quota_guard
        self.client = TestClient(app)

        self._saved_cache_mode = config.CACHE_MODE
        self._saved_pii_enabled = config.PII_SCRUBBING_ENABLED
        self._saved_guardrails_enabled = config.GUARDRAILS_ENABLED
        config.GUARDRAILS_ENABLED = False

    async def _mock_quota_guard(self, request: Request):
        request.state.api_key = "eg-test-key"
        return None

    def tearDown(self):
        app.dependency_overrides.clear()
        config.CACHE_MODE = self._saved_cache_mode
        config.PII_SCRUBBING_ENABLED = self._saved_pii_enabled
        config.GUARDRAILS_ENABLED = self._saved_guardrails_enabled
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_admin_endpoints_enforce_admin_key_and_scopes(self):
        # 1. Missing X-Admin-Key -> 401
        resp_unauth = self.client.get("/api/v1/admin/tenant-policies/")
        self.assertEqual(resp_unauth.status_code, 401)

        read_only_admin = AdminKeyIdentity(
            key_id=2, name="reader", scopes=frozenset({"policy:read"})
        )
        wrong_scope_admin = AdminKeyIdentity(
            key_id=3, name="quota-only", scopes=frozenset({"quota:write"})
        )

        with patch(
            "app.modules.admin.admin_api_keys_service.admin_api_key_service.resolve_admin_key",
            return_value=wrong_scope_admin,
        ):
            r = self.client.get(
                "/api/v1/admin/tenant-policies/",
                headers={"X-Admin-Key": "ega_wrong_scope"},
            )
            self.assertEqual(r.status_code, 403)

        with patch(
            "app.modules.admin.admin_api_keys_service.admin_api_key_service.resolve_admin_key",
            return_value=read_only_admin,
        ):
            # policy:read allowed on GET
            r_get = self.client.get(
                "/api/v1/admin/tenant-policies/",
                headers={"X-Admin-Key": "ega_reader"},
            )
            self.assertEqual(r_get.status_code, 200)

            # policy:read rejected on PUT
            r_put = self.client.put(
                "/api/v1/admin/tenant-policies/acme",
                headers={"X-Admin-Key": "ega_reader"},
                json={"zero_retention": True, "semantic_cache_enabled": False},
            )
            self.assertEqual(r_put.status_code, 403)

        # Full admin upsert -> get -> list -> delete
        with patch(
            "app.modules.admin.admin_api_keys_service.admin_api_key_service.resolve_admin_key",
            return_value=self.admin,
        ):
            r_put = self.client.put(
                "/api/v1/admin/tenant-policies/acme",
                headers={"X-Admin-Key": "ega_admin"},
                json={
                    "semantic_cache_enabled": False,
                    "zero_retention": True,
                    "pii_scrubbing_mode": "enforced",
                    "guardrails_mode": "block",
                    "description": "Strict HIPAA org",
                    "is_active": True,
                },
            )
            self.assertEqual(r_put.status_code, 200)
            body = r_put.json()["response"]
            self.assertEqual(body["tenant_id"], "acme")
            self.assertFalse(body["semantic_cache_enabled"])
            self.assertTrue(body["zero_retention"])

            r_single = self.client.get(
                "/api/v1/admin/tenant-policies/acme",
                headers={"X-Admin-Key": "ega_admin"},
            )
            self.assertEqual(r_single.status_code, 200)

            r_tenants = self.client.get(
                "/api/v1/admin/tenant-policies/tenants",
                headers={"X-Admin-Key": "ega_admin"},
            )
            self.assertEqual(r_tenants.status_code, 200)
            self.assertIn("acme", r_tenants.json()["response"])

            r_del = self.client.delete(
                "/api/v1/admin/tenant-policies/acme",
                headers={"X-Admin-Key": "ega_admin"},
            )
            self.assertEqual(r_del.status_code, 200)

            r_missing = self.client.get(
                "/api/v1/admin/tenant-policies/acme",
                headers={"X-Admin-Key": "ega_admin"},
            )
            self.assertEqual(r_missing.status_code, 404)

    async def test_high_security_tenant_overrides_caller_cache_and_pii_directives(self):
        config.CACHE_MODE = "active"
        config.PII_SCRUBBING_ENABLED = False

        # Admin locks down tenant-hipaa: stop caching, zero-retention, enforce PII scrub
        self.policy_service.upsert_policy(
            "tenant-hipaa",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=False,
                zero_retention=True,
                pii_scrubbing_mode="enforced",
                guardrails_mode="inherit",
                is_active=True,
            ),
            self.admin,
        )

        captured_requests: list[ChatCompletionRequest] = []

        async def fake_provider(self_router, *, request, **kwargs):  # noqa: ANN001, ANN003
            captured_requests.append(request)
            return (
                _dummy_response("Safe zero-retention response"),
                "openai",
                "gpt-4o-mini",
            )

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="user", content="My email is alice@example.com")
            ],
            # Caller explicitly tries to enable caching, logging, and disable PII scrub!
            cache_enabled=True,
            disable_log=False,
            redact_pii=False,
        )
        req.tenant_id = "tenant-hipaa"

        with patch.object(
            LlmRouterService, "_call_provider_with_retry", new=fake_provider
        ):
            resp = await self.router.generate_completion(req, api_key="eg-test-key")
        await self.router.shutdown()

        self.assertEqual(
            resp.choices[0].message.content, "Safe zero-retention response"
        )
        # Upstream got redacted email
        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(
            captured_requests[0].messages[0].content,
            "My email is [EMAIL]",
        )
        # Cache completely bypassed despite caller cache_enabled=True
        self.redis_svc.get.assert_not_awaited()
        self.redis_svc.set.assert_not_awaited()
        self.cache_svc.search_exact.assert_not_called()
        self.cache_svc.upsert.assert_not_called()
        # Zero-retention skipped Postgres writes, kept Redis TPM quota check
        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
        self.quota_svc.record_tokens.assert_awaited_once()

    async def test_performance_tenant_disables_pii_scrubbing_and_keeps_cache(self):
        config.CACHE_MODE = "active"
        config.PII_SCRUBBING_ENABLED = True  # Even with global PII scrubbing ON!

        # Admin configures tenant-fast for savings & latency: cache ON, zero_retention OFF, PII scrubbing OFF
        self.policy_service.upsert_policy(
            "tenant-fast",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=True,
                zero_retention=False,
                pii_scrubbing_mode="disabled",
                guardrails_mode="inherit",
                is_active=True,
            ),
            self.admin,
        )

        captured_requests: list[ChatCompletionRequest] = []

        async def fake_provider(self_router, *, request, **kwargs):  # noqa: ANN001, ANN003
            captured_requests.append(request)
            return _dummy_response("Cached answer"), "openai", "gpt-4o-mini"

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="user", content="Contact bob@example.com for access")
            ],
            redact_pii=True,
        )
        req.tenant_id = "tenant-fast"

        with (
            patch.object(
                LlmRouterService,
                "evaluate_semantic_cache",
                AsyncMock(return_value={"response": None}),
            ),
            patch.object(
                LlmRouterService, "_call_provider_with_retry", new=fake_provider
            ),
        ):
            await self.router.generate_completion(req, api_key="eg-test-key")
        await self.router.shutdown()

        # Prompt was NOT scrubbed because admin disabled PII scrubbing for this tenant
        self.assertEqual(
            captured_requests[0].messages[0].content,
            "Contact bob@example.com for access",
        )
        # Postgres logs were recorded normally
        self.usage_svc.create_llm_usage_log.assert_called_once()
        self.gateway_svc.log_request.assert_called_once()
        self.quota_svc.record_tokens.assert_awaited_once()

    async def test_no_cache_with_billing_logs_tenant(self):
        config.CACHE_MODE = "active"

        # Stop semantic/exact caching while still recording usage & billing logs
        self.policy_service.upsert_policy(
            "tenant-nocache",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=False,
                zero_retention=False,
                pii_scrubbing_mode="inherit",
                is_active=True,
            ),
            self.admin,
        )

        async def fake_provider(self_router, *, request, **kwargs):  # noqa: ANN001, ANN003
            return _dummy_response("Uncached billed response"), "openai", "gpt-4o-mini"

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hello")],
            cache_enabled=True,  # Caller tries to enable cache
        )
        req.tenant_id = "tenant-nocache"

        with patch.object(
            LlmRouterService, "_call_provider_with_retry", new=fake_provider
        ):
            await self.router.generate_completion(req, api_key="eg-test-key")
        await self.router.shutdown()

        # Caching completely stopped
        self.redis_svc.get.assert_not_awaited()
        self.cache_svc.search_exact.assert_not_called()
        self.cache_svc.upsert.assert_not_called()
        # Logs & billing recorded
        self.usage_svc.create_llm_usage_log.assert_called_once()
        self.gateway_svc.log_request.assert_called_once()

    def test_http_endpoints_enforce_resolved_tenant_policy(self):
        config.CACHE_MODE = "active"
        self.policy_service.upsert_policy(
            "org-regulated",
            TenantPrivacyPolicyUpsert(
                semantic_cache_enabled=False,
                zero_retention=True,
                pii_scrubbing_mode="enforced",
                guardrails_mode="inherit",
                is_active=True,
            ),
            self.admin,
        )

        captured: list[ChatCompletionRequest] = []

        async def fake_provider(self_router, *, request, **kwargs):  # noqa: ANN001, ANN003
            captured.append(request)
            return _dummy_response("HTTP strict answer"), "openai", "gpt-4o-mini"

        mock_provider = MagicMock()
        mock_provider.resolve.return_value = Identity(
            tenant_id="org-regulated", user_id="user-1"
        )

        with (
            patch(
                "app.core.middlewares.api_key_from_headers",
                return_value="eg-org-key",
            ),
            patch(
                "app.core.identity.default_provider",
                return_value=mock_provider,
            ),
            patch.object(
                LlmRouterService, "_call_provider_with_retry", new=fake_provider
            ),
        ):
            r_chat = self.client.post(
                "/api/v1/chat/completions",
                headers={"X-API-Key": "eg-org-key"},
                json={
                    "service_name": "openai",
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": "Email bob@regulated.org"}
                    ],
                    "cache_enabled": True,
                },
            )
            self.assertEqual(r_chat.status_code, 200)
            self.assertEqual(r_chat.headers.get(EchoGateHeaders.CACHE), "BYPASS")
            self.assertEqual(r_chat.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
            self.assertEqual(captured[-1].messages[0].content, "Email [EMAIL]")

            r_msg = self.client.post(
                "/api/v1/messages",
                headers={"X-API-Key": "eg-org-key"},
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": "SSN 123-45-6789"}],
                    "cache_enabled": True,
                },
            )
            self.assertEqual(r_msg.status_code, 200)
            self.assertEqual(r_msg.headers.get(EchoGateHeaders.CACHE), "BYPASS")
            self.assertEqual(r_msg.headers.get(EchoGateHeaders.DISABLE_LOG), "true")
            self.assertEqual(captured[-1].messages[0].content, "SSN [SSN]")

        self.usage_svc.create_llm_usage_log.assert_not_called()
        self.gateway_svc.log_request.assert_not_called()
