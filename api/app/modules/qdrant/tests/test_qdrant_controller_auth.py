"""Auth contract for the Qdrant diagnostic search endpoints.

These endpoints return raw cache payloads (prompts and completions) for every
tenant at once and expose no tenant filter, so they must only be reachable with
an admin key holding ``cache:read``. A tenant ``X-API-Key`` must not authorize
them, and neither must an anonymous request.
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.constants import EchoGateHeaders
from app.core.dependencies import (
    get_embedding_service,
    get_llm_cache_collection_service,
)
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.qdrant.qdrant_controller import RequireCacheRead, api_v1_router

ADMIN_KEY = EchoGateHeaders.ADMIN_KEY
READER = AdminKeyIdentity(key_id=1, name="ops", scopes=frozenset({"cache:read"}))
SEARCH_BODY = {"messages": [{"role": "user", "content": "hello"}]}
MATCH = {"id": "point-1", "score": 0.91, "payload": {"user_prompt": "hello"}}


class _StubEmbedder:
    async def get_embedding_async(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def get_sparse_embedding(self, text: str) -> dict[str, list]:
        return {"indices": [0], "values": [0.5]}


class _StubCollection:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    def query_points_dense(self, **kwargs):
        self.calls.append("dense")
        self.kwargs.append(kwargs)
        return [MATCH]

    def query_points_rrf(self, **kwargs):
        self.calls.append("rrf")
        self.kwargs.append(kwargs)
        return [MATCH]


def _app(collection: _StubCollection) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_embedding_service] = lambda: _StubEmbedder()
    app.dependency_overrides[get_llm_cache_collection_service] = lambda: collection
    app.include_router(api_v1_router)
    return app


class TestQdrantSearchRequiresAdminKey(unittest.TestCase):
    def test_anonymous_search_is_401(self):
        collection = _StubCollection()
        with TestClient(_app(collection)) as client:
            response = client.post("/api/v1/qdrant/search", json=SEARCH_BODY)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(collection.calls, [])

    def test_tenant_api_key_does_not_authorize(self):
        collection = _StubCollection()
        with TestClient(_app(collection)) as client:
            response = client.post(
                "/api/v1/qdrant/search",
                json=SEARCH_BODY,
                headers={"X-API-Key": "tenant-key"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(collection.calls, [])

    def test_anonymous_rrf_search_is_401(self):
        collection = _StubCollection()
        with TestClient(_app(collection)) as client:
            response = client.post("/api/v1/qdrant/search/rrf", json=SEARCH_BODY)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(collection.calls, [])

    def test_both_routes_declare_the_cache_read_scope(self):
        routes = [
            route
            for route in api_v1_router.routes
            if getattr(route, "path", "").startswith("/api/v1/qdrant/search")
        ]
        self.assertEqual(
            sorted(route.path for route in routes),
            ["/api/v1/qdrant/search", "/api/v1/qdrant/search/rrf"],
        )
        for route in routes:
            guards = [
                dependency.call
                for dependency in route.dependant.dependencies
                if dependency.call is RequireCacheRead
            ]
            self.assertEqual(len(guards), 1, route.path)

    def test_dense_search_runs_with_a_scoped_identity(self):
        collection = _StubCollection()
        app = _app(collection)
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/qdrant/search",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(collection.calls, ["dense"])
        self.assertTrue(response.json()["response"]["found"])
        self.assertEqual(len(response.json()["response"]["payload"]), 1)
        # The documented similarity floor is actually forwarded now.
        self.assertEqual(collection.kwargs[0]["score_threshold"], 0.9)

    def test_rrf_search_runs_with_a_scoped_identity(self):
        collection = _StubCollection()
        app = _app(collection)
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/qdrant/search/rrf",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(collection.calls, ["rrf"])
        self.assertTrue(response.json()["response"]["found"])
        # RRF scores are rank scores, so the floor goes on the dense prefetch.
        self.assertEqual(collection.kwargs[0]["prefetch_score_threshold"], 0.9)
        self.assertNotIn("score_threshold", collection.kwargs[0])

    def test_explicit_threshold_reaches_both_search_modes(self):
        collection = _StubCollection()
        app = _app(collection)
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        body = {**SEARCH_BODY, "threshold": 0.42}
        with TestClient(app) as client:
            dense = client.post(
                "/api/v1/qdrant/search",
                json=body,
                headers={ADMIN_KEY: "admin-key"},
            )
            rrf = client.post(
                "/api/v1/qdrant/search/rrf",
                json=body,
                headers={ADMIN_KEY: "admin-key"},
            )

        self.assertEqual(dense.status_code, 200, dense.text)
        self.assertEqual(rrf.status_code, 200, rrf.text)
        self.assertEqual(collection.kwargs[0]["score_threshold"], 0.42)
        self.assertEqual(collection.kwargs[1]["prefetch_score_threshold"], 0.42)

    def test_out_of_range_threshold_is_rejected(self):
        app = _app(_StubCollection())
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/qdrant/search",
                json={**SEARCH_BODY, "threshold": 1.5},
                headers={"X-Admin-Key": "admin-key"},
            )

        self.assertEqual(response.status_code, 422)

    def test_dense_search_system_prompt_only_and_filter(self):
        collection = _StubCollection()
        app = _app(collection)
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        body = {
            "messages": [{"role": "system", "content": "system prompt"}],
            "model_filter": "gpt-4o",
        }
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/qdrant/search", json=body, headers={ADMIN_KEY: "admin-key"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(collection.calls, ["dense"])
        self.assertEqual(collection.kwargs[0]["filter_payload"], {"model": "gpt-4o"})
        self.assertEqual(collection.kwargs[0]["using"], "system_prompt_embedding")

    def test_dense_and_rrf_search_no_results(self):
        class _EmptyCollection(_StubCollection):
            def query_points_dense(self, **kwargs):
                return []

            def query_points_rrf(self, **kwargs):
                return []

        app = _app(_EmptyCollection())
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        with TestClient(app) as client:
            resp_dense = client.post(
                "/api/v1/qdrant/search",
                json={"messages": []},
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_dense.status_code, 200)
            self.assertFalse(resp_dense.json()["response"]["found"])

            resp_rrf = client.post(
                "/api/v1/qdrant/search/rrf",
                json={"messages": []},
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_rrf.status_code, 200)
            self.assertFalse(resp_rrf.json()["response"]["found"])

    def test_search_rrf_system_and_model_filter(self):
        collection = _StubCollection()
        app = _app(collection)
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        body = {
            "messages": [
                {"role": "system", "content": "sys prompt"},
                {"role": "user", "content": "user prompt"},
            ],
            "model_filter": "claude-3-5-sonnet",
        }
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/qdrant/search/rrf", json=body, headers={ADMIN_KEY: "admin-key"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(collection.calls, ["rrf"])
        self.assertEqual(
            collection.kwargs[0]["filter_payload"], {"model": "claude-3-5-sonnet"}
        )

    def test_search_error_branches(self):
        from app.modules.inference.inference_client import InferenceUnavailable

        class _ErrorEmbedder:
            async def get_embedding_async(self, text: str):
                raise InferenceUnavailable("inference platform down")

            def get_sparse_embedding(self, text: str):
                raise InferenceUnavailable("inference platform down")

        app = FastAPI()
        app.dependency_overrides[get_embedding_service] = lambda: _ErrorEmbedder()
        app.dependency_overrides[get_llm_cache_collection_service] = lambda: (
            _StubCollection()
        )
        app.dependency_overrides[RequireCacheRead] = lambda: READER
        app.include_router(api_v1_router)

        with TestClient(app) as client:
            resp_dense = client.post(
                "/api/v1/qdrant/search",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_dense.status_code, 503)

            resp_rrf = client.post(
                "/api/v1/qdrant/search/rrf",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_rrf.status_code, 503)

        # Generic 500 error branch
        class _CrashCollection(_StubCollection):
            def query_points_dense(self, **kwargs):
                raise RuntimeError("qdrant crash")

            def query_points_rrf(self, **kwargs):
                raise RuntimeError("qdrant crash")

        app_crash = _app(_CrashCollection())
        app_crash.dependency_overrides[RequireCacheRead] = lambda: READER
        with TestClient(app_crash) as client:
            resp_dense_500 = client.post(
                "/api/v1/qdrant/search",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_dense_500.status_code, 500)

            resp_rrf_500 = client.post(
                "/api/v1/qdrant/search/rrf",
                json=SEARCH_BODY,
                headers={ADMIN_KEY: "admin-key"},
            )
            self.assertEqual(resp_rrf_500.status_code, 500)


if __name__ == "__main__":
    unittest.main()
