"""Identity must come from the API key and nothing else.

Two paths used to let a caller pick its own identity alongside a perfectly valid
key:

* the request body carried ``tenant_id``/``user_id``, which the chat controller
  preferred over the resolved identity and then fed into the cache key and the
  Qdrant tenant/user filters — enough to read another tenant's cached answers;
* the quota guard let ``body["user"]`` overwrite the user it billed, which minted
  a fresh Redis rate-limit bucket per request and split the RPM counter from the
  TPM counter.
"""

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_llm_quota_service, get_llm_router_service
from app.core.guards import llm_quota_guard
from app.core.middlewares import TenantContextMiddleware, UserMiddleware
from app.modules.api_keys.api_keys_service import ApiKeyIdentity
from app.modules.chat.chat_controller import api_v1_router as chat_router
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)


def _request(body: dict, *, tenant_id: str | None, user_id: str | None):
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id=tenant_id, user_id=user_id, user=user_id),
        headers={},
    )
    request.body = lambda: _json_body(body)
    return request


async def _json_body(body: dict) -> bytes:
    return json.dumps(body).encode()


def _quota_service() -> tuple[object, dict]:
    """A quota service that records the arguments it was called with."""
    captured: dict = {}

    async def check_quota(**kwargs):
        captured.update(kwargs)
        return True

    return SimpleNamespace(check_quota=check_quota), captured


def _run_guard(body: dict, *, tenant_id, user_id, x_api_key="eg_valid") -> dict:
    service, captured = _quota_service()
    asyncio.run(
        llm_quota_guard(
            request=_request(body, tenant_id=tenant_id, user_id=user_id),
            x_api_key=x_api_key,
            llm_quota_service=service,
        )
    )
    return captured


# --- The request body cannot choose an identity ------------------------------


def test_body_cannot_set_tenant_or_user_on_the_request():
    request = ChatCompletionRequest(
        model="qwen3:8b",
        messages=[ChatMessage(role="user", content="hi")],
        tenant_id="victim",
        user_id="attacker",
    )
    assert request.tenant_id is None
    assert request.user_id is None


def test_identity_is_not_part_of_the_public_request_contract():
    """The OpenAPI body must not advertise the fields it cannot honour."""
    properties = ChatCompletionRequest.model_json_schema().get("properties", {})
    assert "tenant_id" not in properties
    assert "user_id" not in properties


def test_resolved_identity_is_readable_off_the_request():
    """The controller stamps identity after parsing; services read it back."""
    request = ChatCompletionRequest(
        model="qwen3:8b",
        messages=[ChatMessage(role="user", content="hi")],
    )
    request.tenant_id = "acme"
    request.user_id = "dev"
    assert request.tenant_id == "acme"
    assert request.user_id == "dev"
    # ...but it is not serialized back out to a caller or a provider.
    assert "tenant_id" not in request.model_dump()


# --- The quota guard bills the resolved identity -----------------------------


def test_body_user_cannot_replace_the_authenticated_user():
    captured = _run_guard(
        {
            "service_name": "ollama",
            "model": "qwen3:8b",
            "user": "attacker",
        },
        tenant_id="acme",
        user_id="dev",
    )
    assert captured["user"] == "dev"
    assert captured["tenant_id"] == "acme"


def test_quota_guard_still_reads_provider_and_model_from_the_body():
    captured = _run_guard(
        {"service_name": "ollama", "model": "qwen3:8b", "user": "attacker"},
        tenant_id="acme",
        user_id="dev",
    )
    assert captured["provider"] == "ollama"
    assert captured["model"] == "qwen3:8b"
    assert captured["api_key"] == "eg_valid"


def test_anonymous_request_is_billed_to_the_anonymous_tenant():
    """No key means the middleware set the anonymous tenant; stay there."""
    captured = _run_guard(
        {"service_name": "ollama", "model": "qwen3:8b"},
        tenant_id="anonymous",
        user_id=None,
        x_api_key=None,
    )
    assert captured["tenant_id"] == "anonymous"
    assert captured["api_key"] is None


# --- End to end through the real middleware, guards and controller -----------


def _chat_app(captured: dict) -> FastAPI:
    """A chat app with the real middleware/guards/controller, stubbed backends."""

    class StubRouter:
        async def generate_completion(self, request, api_key=None):
            captured["tenant_id"] = request.tenant_id
            captured["user_id"] = request.user_id
            captured["user"] = request.user
            captured["api_key"] = api_key
            return ChatCompletionResponse(
                id="chatcmpl-1",
                created=0,
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(role="assistant", content="ok"),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class StubQuota:
        async def check_quota(self, **kwargs):
            captured["quota"] = kwargs
            return True

    app = FastAPI()
    # Same order as app/main.py: TenantContextMiddleware is added last, so it is
    # the outermost and runs before UserMiddleware.
    app.add_middleware(UserMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_llm_router_service] = lambda: StubRouter()
    app.dependency_overrides[get_llm_quota_service] = lambda: StubQuota()
    app.include_router(chat_router)
    return app


def _post(app: FastAPI, *, headers: dict, body_extra: dict | None = None) -> dict:
    payload = {
        "service_name": "ollama",
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "hello"}],
        **(body_extra or {}),
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/completions", json=payload, headers=headers
        )
    assert response.status_code == 200, response.text
    return response.json()


def _resolve_key(monkeypatch, identity: ApiKeyIdentity | None) -> None:
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(svc.api_key_service, "resolve_api_key", lambda _key: identity)


def test_valid_key_wins_over_spoofed_headers_and_body(monkeypatch):
    """The original attack: valid key, fake tenant/user headers, fake body."""
    _resolve_key(
        monkeypatch, ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev")
    )
    captured: dict = {}
    app = _chat_app(captured)

    _post(
        app,
        headers={
            "x-api-key": "eg_valid",
            "x-tenant-id": "victim",
            "x-user-id": "attacker",
        },
        body_extra={"tenant_id": "victim", "user_id": "attacker", "user": "attacker"},
    )

    # Routed, scoped and billed as acme/dev.
    assert captured["tenant_id"] == "acme"
    assert captured["user_id"] == "dev"
    assert captured["api_key"] == "eg_valid"
    assert captured["quota"]["tenant_id"] == "acme"
    assert captured["quota"]["user"] == "dev"
    # `request.user` is the OpenAI-compatible end-user field, not identity: it
    # echoes whatever the body said. That is harmless because nothing reads it
    # for routing, scoping or billing (the quota guard above is billed "dev").
    assert captured["user"] == "attacker"


def test_user_falls_back_to_the_resolved_identity(monkeypatch):
    """With no OpenAI `user` in the body, the resolved identity fills in."""
    _resolve_key(
        monkeypatch, ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev")
    )
    captured: dict = {}
    app = _chat_app(captured)

    _post(
        app,
        headers={"x-api-key": "eg_valid", "x-tenant-id": "victim", "x-user-id": "no"},
    )

    assert captured["user"] == "dev"
    assert captured["tenant_id"] == "acme"
    assert captured["user_id"] == "dev"


def test_spoofed_session_id_still_scopes_the_cache(monkeypatch):
    """Session/conversation scope is not identity; it survives for now."""
    _resolve_key(
        monkeypatch, ApiKeyIdentity(api_key_id=1, tenant_id="acme", user_id="dev")
    )
    captured: dict = {}
    app = _chat_app(captured)

    _post(app, headers={"x-api-key": "eg_valid", "x-session-id": "sess-1"})

    assert captured["tenant_id"] == "acme"
    assert captured["user_id"] == "dev"


def test_keyless_request_is_anonymous_even_with_tenant_headers(monkeypatch):
    _resolve_key(monkeypatch, None)
    captured: dict = {}
    app = _chat_app(captured)

    _post(app, headers={"x-tenant-id": "victim", "x-user-id": "attacker"})

    assert captured["tenant_id"] == "anonymous"
    assert captured["user_id"] is None
    assert captured["quota"]["tenant_id"] == "anonymous"


def test_unresolvable_key_is_still_rejected(monkeypatch):
    """Dropping the header path must not have weakened the 401."""
    _resolve_key(monkeypatch, None)
    captured: dict = {}
    app = _chat_app(captured)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/completions",
            json={"model": "qwen3:8b", "messages": []},
            headers={"x-api-key": "eg_bogus", "x-tenant-id": "victim"},
        )

    assert response.status_code == 401
    assert "tenant_id" not in captured
