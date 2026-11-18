"""Provider-native error envelopes for the SDK completion surfaces.

`/api/v1/chat/completions` must answer in the OpenAI shape and `/api/v1/messages`
in the Anthropic shape, so SDK clients raise typed errors instead of failing to
parse `{"detail": ...}`. Gateway-native routes keep FastAPI's default body.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from app.core.dependencies import get_llm_router_service
from app.core.error_envelopes import surface_for_path
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.llm.llm_router_service import LlmRouterService

CHAT = "/api/v1/chat/completions"
MESSAGES = "/api/v1/messages"

CHAT_BODY = {
    "service_name": "openai",
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hi"}],
}
MESSAGES_BODY = {
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "Hi"}],
}

PROVIDER_FAILURE = HTTPException(
    status_code=503,
    detail={
        "message": "Provider 'openai' is temporarily unavailable (503): down",
        "error_code": "provider_unavailable",
        "provider": "openai",
    },
    headers={"Retry-After": "5"},
)


@pytest.fixture
def router():
    mock = MagicMock(spec=LlmRouterService)
    app.dependency_overrides[get_llm_router_service] = lambda: mock
    app.dependency_overrides[llm_quota_guard] = lambda: None
    try:
        yield mock
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/chat/completions", "openai"),
        ("/api/v1/chat/completions/", "openai"),
        ("/api/v1/messages", "anthropic"),
        ("/v1/messages", "anthropic"),
        ("/api/v1/chat/estimate", None),
        ("/api/v1/chat/completions/semantic-search", None),
        ("/api/v1/admin/cache/evict", None),
    ],
)
def test_surface_detection(path, expected):
    assert surface_for_path(path) == expected


# --- OpenAI surface ----------------------------------------------------------


def test_provider_failure_uses_openai_envelope(client, router):
    router.generate_completion = AsyncMock(side_effect=PROVIDER_FAILURE)

    response = client.post(CHAT, json=CHAT_BODY)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "server_error"
    assert error["code"] == "provider_unavailable"
    assert "temporarily unavailable" in error["message"]
    assert "param" not in error
    assert response.headers["retry-after"] == "5"
    assert response.headers["x-echogate-error-code"] == "provider_unavailable"
    assert response.headers["x-gateway-degradation"] == "hard_down"


def test_quota_rejection_keeps_headers_and_is_rate_limit_error(client, router):
    router.generate_completion = AsyncMock(
        side_effect=HTTPException(
            status_code=429,
            detail="Rate limit exceeded (Limit: 10 RPM)",
            headers={
                "Retry-After": "30",
                "X-RateLimit-Remaining-Requests": "0",
            },
        )
    )

    response = client.post(CHAT, json=CHAT_BODY)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "rate_limit_error"
    assert error["message"] == "Rate limit exceeded (Limit: 10 RPM)"
    assert "code" not in error
    assert response.headers["retry-after"] == "30"
    assert response.headers["x-ratelimit-remaining-requests"] == "0"
    assert response.headers["x-gateway-degradation"] == "none"


def test_validation_failure_uses_openai_envelope(client, router):
    response = client.post(
        CHAT, json={"service_name": "openai", "model": "gpt-4o-mini"}
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "messages"
    assert "messages" in error["message"]


def test_unknown_key_is_an_openai_authentication_error(client, router, monkeypatch):
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(svc.api_key_service, "resolve_api_key", lambda _key: None)

    response = client.post(CHAT, json=CHAT_BODY, headers={"x-api-key": "eg_unknown"})

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["type"] == "authentication_error"
    assert "Invalid or inactive" in error["message"]


# --- Anthropic surface -------------------------------------------------------


def test_provider_failure_uses_anthropic_envelope(client, router):
    router.generate_completion = AsyncMock(side_effect=PROVIDER_FAILURE)

    response = client.post(MESSAGES, json=MESSAGES_BODY)

    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "overloaded_error"
    assert "temporarily unavailable" in body["error"]["message"]
    assert "code" not in body["error"]
    assert response.headers["retry-after"] == "5"
    assert response.headers["x-echogate-error-code"] == "provider_unavailable"
    assert response.headers["x-gateway-degradation"] == "hard_down"


def test_anthropic_rate_limit_envelope(client, router):
    router.generate_completion = AsyncMock(
        side_effect=HTTPException(status_code=429, detail="Too many requests")
    )

    response = client.post(MESSAGES, json=MESSAGES_BODY)

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


def test_anthropic_validation_envelope(client, router):
    response = client.post(MESSAGES, json={"model": "claude-3-5-sonnet-20241022"})

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "messages" in body["error"]["message"]


def test_root_v1_messages_alias_uses_anthropic_envelope(client, router):
    router.generate_completion = AsyncMock(side_effect=PROVIDER_FAILURE)

    response = client.post("/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "overloaded_error"


# --- Gateway-native surfaces -------------------------------------------------


def test_non_sdk_surface_keeps_fastapi_detail(client, router):
    response = client.post("/api/v1/chat/estimate", json={"model": "gpt-4o-mini"})

    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert "error" not in body
