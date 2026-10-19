"""Verify the tenant context established by the middleware reaches the endpoint.

The RLS hook reads the contextvar at transaction-begin time, so it must survive
the ASGI call chain (and any thread-pool hop). These tests use a bare app so they
need no Redis/Qdrant/Postgres.

``X-API-Key`` is the only source of identity. ``X-Tenant-ID``/``X-User-ID`` are
caller-supplied and are ignored outright, so a request cannot pick the tenant it
runs as.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middlewares import TenantContextMiddleware
from app.core.rls import get_rls_context
from app.modules.api_keys.api_keys_service import ApiKeyIdentity

app = FastAPI()
app.add_middleware(TenantContextMiddleware)


@app.get("/ctx")
def read_context():
    context = get_rls_context()
    return {"tenant_id": context.tenant_id, "user_id": context.user_id}


client = TestClient(app)


@pytest.fixture
def resolved_key(monkeypatch):
    """Stub API key resolution so no database is needed."""

    def _install(identity: ApiKeyIdentity | None) -> None:
        import app.modules.api_keys.api_keys_service as svc

        monkeypatch.setattr(
            svc.api_key_service, "resolve_api_key", lambda _key: identity
        )

    return _install


def _identity(tenant_id: str, user_id: str | None) -> ApiKeyIdentity:
    return ApiKeyIdentity(api_key_id=1, tenant_id=tenant_id, user_id=user_id)


def test_api_key_identity_propagates_to_endpoint(resolved_key):
    resolved_key(_identity("acme", "user-1"))
    response = client.get("/ctx", headers={"x-api-key": "eg_valid"})
    assert response.json() == {"tenant_id": "acme", "user_id": "user-1"}


def test_missing_key_is_anonymous(resolved_key):
    resolved_key(_identity("acme", "user-1"))
    response = client.get("/ctx")
    assert response.json() == {"tenant_id": "anonymous", "user_id": None}


def test_identity_headers_cannot_override_a_valid_key(resolved_key):
    resolved_key(_identity("acme", "user-1"))
    response = client.get(
        "/ctx",
        headers={
            "x-api-key": "eg_valid",
            "x-tenant-id": "victim",
            "x-user-id": "attacker",
        },
    )
    assert response.json() == {"tenant_id": "acme", "user_id": "user-1"}


def test_keyless_caller_cannot_claim_a_tenant_with_headers(resolved_key):
    """No credential means anonymous, whatever the headers claim."""
    resolved_key(None)
    response = client.get(
        "/ctx", headers={"x-tenant-id": "victim", "x-user-id": "attacker"}
    )
    assert response.json() == {"tenant_id": "anonymous", "user_id": None}


def test_key_without_a_user_stays_userless(resolved_key):
    resolved_key(_identity("acme", None))
    response = client.get("/ctx", headers={"x-api-key": "eg_valid"})
    assert response.json() == {"tenant_id": "acme", "user_id": None}


def test_context_does_not_leak_between_requests(resolved_key):
    resolved_key(_identity("acme", "user-1"))
    client.get("/ctx", headers={"x-api-key": "eg_valid"})
    response = client.get("/ctx")
    assert response.json() == {"tenant_id": "anonymous", "user_id": None}
