"""Verify the tenant context established by the middleware reaches the endpoint.

The RLS hook reads the contextvar at transaction-begin time, so it must survive
the ASGI call chain (and any thread-pool hop). These tests use a bare app so they
need no Redis/Qdrant/Postgres.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middlewares import TenantContextMiddleware
from app.core.rls import get_rls_context

app = FastAPI()
app.add_middleware(TenantContextMiddleware)


@app.get("/ctx")
def read_context():
    context = get_rls_context()
    return {"tenant_id": context.tenant_id, "user_id": context.user_id}


client = TestClient(app)


def test_header_identity_propagates_to_endpoint():
    response = client.get(
        "/ctx", headers={"x-tenant-id": "acme", "x-user-id": "user-1"}
    )
    assert response.json() == {"tenant_id": "acme", "user_id": "user-1"}


def test_context_does_not_leak_between_requests():
    client.get("/ctx", headers={"x-tenant-id": "acme", "x-user-id": "user-1"})
    response = client.get("/ctx")
    assert response.json() == {"tenant_id": "anonymous", "user_id": None}
