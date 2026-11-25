"""Tests for anonymous provider allowlist and model catalog filtering."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.config import Config, config
from app.core.dependencies import get_llm_provider_service, get_llm_router_service
from app.core.error_envelopes import install_error_envelopes
from app.core.guards import llm_quota_guard
from app.modules.anthropic.anthropic_controller import anthropic_router
from app.modules.api_keys.api_keys_service import ApiKeyIdentity
from app.modules.chat.chat_controller import api_v1_router as chat_router
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm.models_controller import models_router


class _StubProviderService:
    def __init__(self, availability: dict[str, bool]):
        self._availability = availability

    def service_availability(self) -> dict[str, bool]:
        return dict(self._availability)


def test_config_anonymous_allowed_providers_defaults_and_overrides():
    env_keys = (
        "ANONYMOUS_ALLOWED_PROVIDERS",
        "SUPABASE_AUTH_ENABLED",
        "SUPABASE_JWT_SECRET",
        "PYTHON_ENV",
    )
    saved = {k: os.environ.get(k) for k in env_keys}
    try:
        for k in env_keys:
            os.environ.pop(k, None)

        # Local OSS default without Supabase keeps existing unrestricted behavior
        local_cfg = Config()
        assert local_cfg.ANONYMOUS_ALLOWED_PROVIDERS is None

        # SaaS / Supabase mode automatically restricts anonymous callers to groq & openrouter
        os.environ["SUPABASE_AUTH_ENABLED"] = "true"
        saas_cfg = Config()
        assert saas_cfg.ANONYMOUS_ALLOWED_PROVIDERS == {"groq", "openrouter"}

        # Production mode also defaults to groq & openrouter
        os.environ.pop("SUPABASE_AUTH_ENABLED", None)
        os.environ["PYTHON_ENV"] = "production"
        prod_cfg = Config()
        assert prod_cfg.ANONYMOUS_ALLOWED_PROVIDERS == {"groq", "openrouter"}

        # Explicit "*" allows all providers
        os.environ["ANONYMOUS_ALLOWED_PROVIDERS"] = "*"
        wildcard_cfg = Config()
        assert wildcard_cfg.ANONYMOUS_ALLOWED_PROVIDERS is None

        # Explicit comma-separated list overrides default
        os.environ["ANONYMOUS_ALLOWED_PROVIDERS"] = "groq, openrouter, ollama"
        custom_cfg = Config()
        assert custom_cfg.ANONYMOUS_ALLOWED_PROVIDERS == {
            "groq",
            "openrouter",
            "ollama",
        }
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_models_endpoint_filters_disallowed_providers_for_anonymous(monkeypatch):
    monkeypatch.setattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", {"groq", "openrouter"})
    monkeypatch.setattr(config, "ALLOW_ANONYMOUS", True)

    availability = {
        "groq": True,
        "openrouter": True,
        "openai": True,
        "claude": True,
        "anthropic": True,
        "gemini": True,
        "google-genai": True,
        "ollama": True,
    }
    app = FastAPI()
    app.include_router(models_router)
    app.dependency_overrides[get_llm_provider_service] = lambda: _StubProviderService(
        availability
    )
    client = TestClient(app)

    # Anonymous caller sees only groq & openrouter models + aliases with allowed targets
    anon_payload = client.get("/api/v1/models").json()
    anon_ids = {entry["id"] for entry in anon_payload["data"]}
    assert "openai/gpt-oss-20b" in anon_ids
    assert "qwen/qwen3.8-27b:free" in anon_ids
    assert "fast" in anon_ids
    assert "cheap" in anon_ids
    assert "quality" not in anon_ids
    assert "gpt-4o-mini" not in anon_ids
    assert "gemini-3.1-flash-lite" not in anon_ids
    assert "claude-3-7-sonnet-20250219" not in anon_ids

    fast_entry = next(e for e in anon_payload["data"] if e["id"] == "fast")
    assert fast_entry["providers"] == ["groq"]

    # Authenticated caller with a valid API key sees all configured providers
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(
        svc.api_key_service,
        "resolve_api_key",
        lambda _key: ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev"),
    )
    auth_payload = client.get(
        "/api/v1/models", headers={"X-API-Key": "eg_valid"}
    ).json()
    auth_ids = {entry["id"] for entry in auth_payload["data"]}
    assert "gpt-4o-mini" in auth_ids
    assert "gemini-3.1-flash-lite" in auth_ids
    assert "claude-3-7-sonnet-20250219" in auth_ids
    assert "quality" in auth_ids


def _chat_test_app():
    router = LlmRouterService(
        llm_provider_service=MagicMock(),
        embedding_service=MagicMock(),
        llm_cache_service=MagicMock(),
        redis_service=MagicMock(),
        reranker_service=MagicMock(),
        intent_classifier_service=MagicMock(),
        entity_extractor_service=MagicMock(),
        llm_usage_service=MagicMock(),
    )
    mock_resp = ChatCompletionResponse(
        id="chatcmpl-test",
        created=123456,
        model="openai/gpt-oss-20b",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content="ok"),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        cache_info={"matched": False},
    )
    router.generate_completion = AsyncMock(return_value=mock_resp)

    app = FastAPI()
    install_error_envelopes(app)
    app.include_router(chat_router)
    app.include_router(anthropic_router)
    app.dependency_overrides[get_llm_router_service] = lambda: router
    app.dependency_overrides[llm_quota_guard] = lambda: None
    return app, router


@pytest.mark.parametrize(
    ("service_name", "model"),
    [
        ("openai", "gpt-4o-mini"),
        ("claude", "claude-3-7-sonnet-20250219"),
        ("anthropic", "claude-3-7-sonnet-20250219"),
        ("google-genai", "gemini-3.1-flash-lite"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("ollama", "qwen3:8b"),
    ],
)
def test_anonymous_chat_rejects_paid_and_restricted_providers(
    monkeypatch, service_name: str, model: str
):
    monkeypatch.setattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", {"groq", "openrouter"})
    monkeypatch.setattr(config, "ALLOW_ANONYMOUS", True)

    app, router = _chat_test_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/chat/completions",
        json={
            "service_name": service_name,
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["type"] == "permission_error"
    assert body["error"]["code"] == "provider_not_allowed"
    router.generate_completion.assert_not_awaited()


def test_anonymous_chat_allows_groq_and_openrouter_and_blocks_quality_alias(
    monkeypatch,
):
    monkeypatch.setattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", {"groq", "openrouter"})
    monkeypatch.setattr(config, "ALLOW_ANONYMOUS", True)

    app, router = _chat_test_app()
    client = TestClient(app)

    ok_groq = client.post(
        "/api/v1/chat/completions",
        json={
            "service_name": "groq",
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert ok_groq.status_code == 200
    called_req = router.generate_completion.await_args.args[0]
    assert called_req.allowed_providers == frozenset({"groq", "openrouter"})

    ok_openrouter = client.post(
        "/api/v1/chat/completions",
        json={
            "service_name": "openrouter",
            "model": "qwen/qwen3.8-27b:free",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert ok_openrouter.status_code == 200

    blocked_alias = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "quality",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert blocked_alias.status_code == 403


def test_anonymous_anthropic_messages_rejects_claude(monkeypatch):
    monkeypatch.setattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", {"groq", "openrouter"})
    monkeypatch.setattr(config, "ALLOW_ANONYMOUS", True)

    app, router = _chat_test_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/messages",
        json={
            "model": "claude-3-7-sonnet-20250219",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["type"] == "permission_error"
    router.generate_completion.assert_not_awaited()


def test_authenticated_caller_can_use_openai_and_claude(monkeypatch):
    monkeypatch.setattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", {"groq", "openrouter"})
    monkeypatch.setattr(config, "ALLOW_ANONYMOUS", True)

    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(
        svc.api_key_service,
        "resolve_api_key",
        lambda _key: ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev"),
    )

    app, _ = _chat_test_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/chat/completions",
        headers={"X-API-Key": "eg_valid"},
        json={
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_router_clamps_fallback_to_allowed_providers(monkeypatch):
    monkeypatch.setattr(config, "USE_FALLBACK_LLM", "true")
    monkeypatch.setattr(config, "FALLBACK_LLM_SERVICE", "google-genai")
    monkeypatch.setattr(config, "FALLBACK_LLM_MODEL", "gemini-3.1-flash-lite")

    provider_svc = MagicMock()
    provider_svc.generate_groq_completion = AsyncMock(
        side_effect=RuntimeError("groq unavailable")
    )
    provider_svc.generate_gemini_completion = AsyncMock()

    router = LlmRouterService(
        llm_provider_service=provider_svc,
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
    req.allowed_providers = frozenset({"groq", "openrouter"})

    with pytest.raises(HTTPException):
        await router._call_provider_with_retry(
            request=req,
            service_name="groq",
            fallback_service="google-genai",
            fallback_model="gemini-3.1-flash-lite",
        )

    provider_svc.generate_gemini_completion.assert_not_awaited()
