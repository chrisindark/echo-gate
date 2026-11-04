"""Auth and tenant targeting for the chat cache diagnostics.

``semantic-search``, ``exact-search`` and ``cache-save`` used to be reachable
without any credential (``semantic-search`` only had the *optional* tenant
guard), so an anonymous caller could read cached answers out of the shared
anonymous scope and, through ``cache-save``, write arbitrary completions into
it. They are operator tooling: reads need ``cache:read``, the write needs
``cache:write``, and a tenant ``X-API-Key`` is not accepted on any of them.

Tenant targeting is explicit because an admin key never establishes a tenant
identity: reads take ``?tenant_id=`` and ``cache-save`` takes ``tenant_id`` in
the body.
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.chat.chat_controller import (
    RequireCacheRead,
    RequireCacheWrite,
    api_v1_router,
)

ADMIN_KEY = EchoGateHeaders.ADMIN_KEY
READER = AdminKeyIdentity(key_id=1, name="reader", scopes=frozenset({"cache:read"}))
WRITER = AdminKeyIdentity(key_id=2, name="writer", scopes=frozenset({"cache:write"}))

REQUEST_BODY = {
    "service_name": "ollama",
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "hello"}],
}

RESPONSE_BODY = {
    "id": "chatcmpl-1",
    "created": 0,
    "model": "qwen3:8b",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "cached answer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

SAVE_BODY = {"request": REQUEST_BODY, "response": RESPONSE_BODY}

PATHS = (
    "/api/v1/chat/completions/semantic-search",
    "/api/v1/chat/completions/exact-search",
)


class _StubRouter:
    """Records the tenant each diagnostic actually ran as."""

    def __init__(self) -> None:
        self.tenants: list[str | None] = []
        self.saved: list[str | None] = []

    async def evaluate_semantic_cache(self, request, force: bool = False):
        self.tenants.append(request.tenant_id)
        return {"matches": [], "intent": None}

    async def get_exact_match(self, request):
        self.tenants.append(request.tenant_id)
        return None, None

    async def save_to_qdrant_cache(self, request, response):
        self.saved.append(request.tenant_id)
        return "point-1", True


def _app(router: _StubRouter, *, authorize: AdminKeyIdentity | None = None) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_llm_router_service] = lambda: router
    if authorize is not None:
        app.dependency_overrides[RequireCacheRead] = lambda: authorize
        app.dependency_overrides[RequireCacheWrite] = lambda: authorize
    app.include_router(api_v1_router)
    return app


class TestDiagnosticsRejectAnonymousAndTenantKeys(unittest.TestCase):
    def test_reads_reject_anonymous(self):
        for path in PATHS:
            with self.subTest(path=path):
                router = _StubRouter()
                with TestClient(_app(router)) as client:
                    response = client.post(path, json=REQUEST_BODY)

                self.assertEqual(response.status_code, 401)
                self.assertEqual(router.tenants, [])

    def test_reads_reject_a_tenant_api_key(self):
        for path in PATHS:
            with self.subTest(path=path):
                router = _StubRouter()
                with TestClient(_app(router)) as client:
                    response = client.post(
                        path,
                        json=REQUEST_BODY,
                        headers={"X-API-Key": "tenant-key"},
                    )

                self.assertEqual(response.status_code, 401)
                self.assertEqual(router.tenants, [])

    def test_cache_save_rejects_anonymous_and_tenant_keys(self):
        for headers in ({}, {"X-API-Key": "tenant-key"}):
            with self.subTest(headers=headers):
                router = _StubRouter()
                with TestClient(_app(router)) as client:
                    response = client.post(
                        "/api/v1/chat/completions/cache-save",
                        json=SAVE_BODY,
                        headers=headers,
                    )

                self.assertEqual(response.status_code, 401)
                self.assertEqual(router.saved, [])


class TestAdminKeyAndTenantTargeting(unittest.TestCase):
    def test_reads_run_as_the_requested_tenant(self):
        for path in PATHS:
            with self.subTest(path=path):
                router = _StubRouter()
                with TestClient(_app(router, authorize=READER)) as client:
                    response = client.post(
                        f"{path}?tenant_id=acme",
                        json=REQUEST_BODY,
                        headers={ADMIN_KEY: "admin"},
                    )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(router.tenants, ["acme"])

    def test_reads_without_a_tenant_use_the_resolved_scope(self):
        router = _StubRouter()
        with TestClient(_app(router, authorize=READER)) as client:
            response = client.post(
                PATHS[0],
                json=REQUEST_BODY,
                headers={ADMIN_KEY: "admin"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        # No TenantContextMiddleware in this harness, so nothing resolved.
        self.assertEqual(router.tenants, [None])

    def test_cache_save_honours_the_body_tenant(self):
        router = _StubRouter()
        with TestClient(_app(router, authorize=WRITER)) as client:
            response = client.post(
                "/api/v1/chat/completions/cache-save",
                json={**SAVE_BODY, "tenant_id": "acme"},
                headers={ADMIN_KEY: "admin"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(router.saved, ["acme"])
        self.assertEqual(response.json()["response"], {"point_id": "point-1"})

    def test_cache_save_without_a_tenant_writes_the_resolved_scope(self):
        router = _StubRouter()
        with TestClient(_app(router, authorize=WRITER)) as client:
            response = client.post(
                "/api/v1/chat/completions/cache-save",
                json=SAVE_BODY,
                headers={ADMIN_KEY: "admin"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(router.saved, [None])

    def test_each_route_declares_its_own_scope_guard(self):
        """cache:read is not wired to the write route, or vice versa."""
        expected = {
            "/api/v1/chat/completions/semantic-search": RequireCacheRead,
            "/api/v1/chat/completions/exact-search": RequireCacheRead,
            "/api/v1/chat/completions/cache-save": RequireCacheWrite,
        }
        routes = {route.path: route for route in api_v1_router.routes}
        for path, guard in expected.items():
            with self.subTest(path=path):
                calls = [dep.call for dep in routes[path].dependant.dependencies]
                self.assertIn(guard, calls)
                other = (
                    RequireCacheWrite if guard is RequireCacheRead else RequireCacheRead
                )
                self.assertNotIn(other, calls)


if __name__ == "__main__":
    unittest.main()
