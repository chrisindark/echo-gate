"""Model discovery: GET /v1/models and /api/v1/models.

Covers the OpenAI envelope, the Anthropic envelope selected by
``anthropic-version``, alias expansion, and the admin gate on
``?include_unavailable=true``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_llm_provider_service
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.api_keys.api_keys_service import ApiKeyIdentity
from app.modules.llm.model_registry import model_registry
from app.modules.llm.models_controller import models_router


class _ProviderService:
    def __init__(self, availability: dict[str, bool]):
        self._availability = availability

    def service_availability(self) -> dict[str, bool]:
        return dict(self._availability)


def _client(availability: dict[str, bool]) -> TestClient:
    app = FastAPI()
    app.include_router(models_router)
    app.dependency_overrides[get_llm_provider_service] = lambda: _ProviderService(
        availability
    )
    return TestClient(app)


def _ids(payload: dict) -> list[str]:
    return [entry["id"] for entry in payload["data"]]


def _entry(payload: dict, model_id: str) -> dict:
    return next(e for e in payload["data"] if e["id"] == model_id)


@pytest.fixture
def disabled_model():
    """Register a registry model and disable it for the test."""
    model_registry.register_model("ollama", "test-disabled-model")
    model_registry.toggle_model("ollama", "test-disabled-model", enabled=False)
    try:
        yield "test-disabled-model"
    finally:
        model_registry.unregister_model("ollama", "test-disabled-model")
        model_registry.toggle_model("ollama", "test-disabled-model", enabled=True)


def _grant_admin(monkeypatch) -> None:
    import app.modules.admin.admin_api_keys_service as admin_svc

    monkeypatch.setattr(
        admin_svc.admin_api_key_service,
        "resolve_admin_key",
        lambda _key: AdminKeyIdentity(
            key_id=1, name="test-admin", scopes=frozenset({"keys:read"})
        ),
    )


# --- OpenAI envelope ---------------------------------------------------------


def test_openai_envelope_lists_configured_models():
    client = _client({"ollama": True})

    response = client.get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert "qwen3:8b" in _ids(payload)

    card = _entry(payload, "qwen3:8b")
    assert card["object"] == "model"
    assert card["created"] == 0
    assert card["owned_by"] == "ollama"
    assert card["available"] is True
    assert card["enabled"] is True
    assert card["providers"] == ["ollama"]
    assert card["context_window"] == 32768
    assert card["targets"] is None


def test_unconfigured_providers_are_hidden_by_default():
    client = _client({"ollama": True})

    payload = client.get("/v1/models").json()

    assert "gpt-4o-mini" not in _ids(payload)
    # Every default alias routes to at least one unconfigured provider.
    assert "fast" not in _ids(payload)


def test_alias_is_listed_when_any_target_is_configured():
    client = _client({"openai": True})

    payload = client.get("/v1/models").json()

    alias = _entry(payload, "fast")
    assert alias["owned_by"] == "echo-gate"
    assert alias["available"] is True
    assert alias["enabled"] is True
    assert alias["providers"] == ["groq", "google-genai", "openai"]
    assert [target["service"] for target in alias["targets"]] == alias["providers"]
    assert [target["available"] for target in alias["targets"]] == [
        False,
        False,
        True,
    ]


def test_model_served_under_two_service_names_is_listed_once():
    client = _client({"gemini": False, "google-genai": True})

    payload = client.get("/v1/models").json()

    gemini_ids = [
        model_id for model_id in _ids(payload) if model_id.startswith("gemini-")
    ]
    assert len(gemini_ids) == len(set(gemini_ids))
    card = _entry(payload, "gemini-3.1-flash-lite")
    assert card["providers"] == ["gemini", "google-genai"]
    assert card["available"] is True


def test_api_v1_alias_serves_the_same_list():
    client = _client({"ollama": True})

    assert _ids(client.get("/v1/models").json()) == _ids(
        client.get("/api/v1/models").json()
    )


# --- Unavailable / disabled --------------------------------------------------


def test_disabled_and_unconfigured_models_are_hidden_by_default(disabled_model):
    client = _client({"ollama": True})

    ids = _ids(client.get("/v1/models").json())

    assert disabled_model not in ids
    assert "gpt-4o-mini" not in ids


def test_include_unavailable_requires_an_admin_key(disabled_model):
    client = _client({"ollama": True})

    response = client.get("/v1/models?include_unavailable=true")

    assert response.status_code == 401


def test_include_unavailable_exposes_disabled_and_unconfigured(
    disabled_model, monkeypatch
):
    _grant_admin(monkeypatch)
    client = _client({"ollama": True})

    response = client.get(
        "/v1/models?include_unavailable=true",
        headers={"x-admin-key": "admin-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    broken = _entry(payload, disabled_model)
    assert broken["enabled"] is False
    assert broken["available"] is True
    unconfigured = _entry(payload, "gpt-4o-mini")
    assert unconfigured["available"] is False


# --- Anthropic envelope ------------------------------------------------------


def test_anthropic_version_selects_the_anthropic_envelope():
    client = _client({"openai": True})

    response = client.get("/v1/models", headers={"anthropic-version": "2023-06-01"})

    assert response.status_code == 200
    payload = response.json()
    assert "object" not in payload
    assert payload["has_more"] is False
    assert payload["first_id"] == payload["data"][0]["id"]
    assert payload["last_id"] == payload["data"][-1]["id"]

    card = payload["data"][0]
    assert card["type"] == "model"
    assert card["display_name"] == card["id"]
    assert card["created_at"] == "1970-01-01T00:00:00Z"
    assert "gpt-4o-mini" in [entry["id"] for entry in payload["data"]]


# --- Tenant auth -------------------------------------------------------------


def test_unknown_tenant_key_is_rejected(monkeypatch):
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(svc.api_key_service, "resolve_api_key", lambda _key: None)
    client = _client({"ollama": True})

    response = client.get("/v1/models", headers={"x-api-key": "eg_unknown"})

    assert response.status_code == 401


def test_anthropic_x_api_key_authenticates(monkeypatch):
    """The Anthropic SDK sends x-api-key, which is X-API-Key case-insensitively."""
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(
        svc.api_key_service,
        "resolve_api_key",
        lambda _key: ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev"),
    )
    client = _client({"openai": True})

    response = client.get(
        "/v1/models",
        headers={"x-api-key": "eg_valid", "anthropic-version": "2023-06-01"},
    )

    assert response.status_code == 200
