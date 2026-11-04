import unittest

from starlette.testclient import TestClient

from app.main import app
from app.modules.health.health_service import (
    check_celery_broker,
    check_celery_workers,
    check_database,
    check_inference,
    check_redis,
    check_vector_db,
)


class TestHealthEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.clear()

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health_live_endpoint(self):
        """Verify /api/v1/health/live returns HTTP 200 with alive status."""
        response = self.client.get("/api/v1/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})

    def test_health_ready_healthy(self):
        """Verify /api/v1/health/ready returns HTTP 200 when Postgres is healthy."""
        app.dependency_overrides[check_database] = lambda: True

        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["checks"], {"database": "ok"})

    def test_health_ready_database_failure(self):
        """Verify /api/v1/health/ready returns HTTP 503 when Postgres fails."""
        app.dependency_overrides[check_database] = lambda: False

        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(data["checks"], {"database": "failed"})

    def test_health_ready_ignores_degradable_dependencies(self):
        """A Redis or Qdrant blip must not take the replica out of rotation.

        Neither dependency can produce an incorrect answer -- both degrade to a
        cache miss -- so neither belongs in the readiness gate.
        """
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_redis] = lambda: False
        app.dependency_overrides[check_vector_db] = lambda: False

        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_health_deps_healthy(self):
        """Verify /api/v1/health/deps reports every dependency as ok."""
        for check in (
            check_database,
            check_redis,
            check_vector_db,
            check_celery_broker,
            check_celery_workers,
            check_inference,
        ):
            app.dependency_overrides[check] = lambda: True

        response = self.client.get("/api/v1/health/deps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["gating"], ["database"])
        self.assertEqual(data["failed"], [])
        self.assertEqual(
            data["checks"],
            {
                "database": "ok",
                "redis": "ok",
                "vector_db": "ok",
                "celery_broker": "ok",
                "inference": "ok",
            },
        )

    def test_health_deps_degraded_still_returns_200(self):
        """A degradable failure is 'degraded' but must never produce a 503."""
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_redis] = lambda: False
        app.dependency_overrides[check_vector_db] = lambda: True
        app.dependency_overrides[check_celery_broker] = lambda: True
        app.dependency_overrides[check_celery_workers] = lambda: True
        app.dependency_overrides[check_inference] = lambda: True

        response = self.client.get("/api/v1/health/deps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["failed"], ["redis"])
        self.assertEqual(data["checks"]["redis"], "failed")

    def test_health_deps_dead_engine_degrades_without_eviction(self):
        """A runtime engine outage must alert, not pull replicas out of rotation.

        Every role it serves degrades to a provider answer, so it is `degraded`
        on `/deps` rather than gating on `/ready`. Boot is where it is fatal:
        `InferenceClient.verify()` refuses to start without an engine.
        """
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_redis] = lambda: True
        app.dependency_overrides[check_vector_db] = lambda: True
        app.dependency_overrides[check_celery_broker] = lambda: True
        app.dependency_overrides[check_celery_workers] = lambda: True
        app.dependency_overrides[check_inference] = lambda: False

        response = self.client.get("/api/v1/health/deps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["failed"], ["inference"])

        # ...and `/ready` stays green, because the gateway can still answer.
        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 200)

    def test_health_deps_unhealthy_still_returns_200(self):
        """A gating failure reports 'unhealthy' for alerting, without a 503."""
        app.dependency_overrides[check_database] = lambda: False
        app.dependency_overrides[check_redis] = lambda: True
        app.dependency_overrides[check_vector_db] = lambda: True
        app.dependency_overrides[check_celery_broker] = lambda: True
        app.dependency_overrides[check_celery_workers] = lambda: True
        app.dependency_overrides[check_inference] = lambda: True

        response = self.client.get("/api/v1/health/deps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(data["failed"], ["database"])

    def test_health_deps_everything_down(self):
        """Verify /api/v1/health/deps stays 200 and lists every failed check."""
        for check in (
            check_database,
            check_redis,
            check_vector_db,
            check_celery_broker,
            check_celery_workers,
            check_inference,
        ):
            app.dependency_overrides[check] = lambda: False

        response = self.client.get("/api/v1/health/deps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(
            data["failed"],
            ["celery_broker", "database", "inference", "redis", "vector_db"],
        )

    def test_health_worker_all_healthy(self):
        """Verify /api/v1/health/worker returns HTTP 200 when Celery broker and workers are healthy."""
        app.dependency_overrides[check_celery_broker] = lambda: True
        app.dependency_overrides[check_celery_workers] = lambda: True

        response = self.client.get("/api/v1/health/worker")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(
            data["checks"],
            {"celery_broker": "ok", "celery_workers": "ok"},
        )

    def test_health_worker_broker_failure(self):
        """Verify /api/v1/health/worker returns HTTP 503 when Celery broker fails."""
        app.dependency_overrides[check_celery_broker] = lambda: False
        app.dependency_overrides[check_celery_workers] = lambda: False

        response = self.client.get("/api/v1/health/worker")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(
            data["checks"],
            {"celery_broker": "failed", "celery_workers": "failed"},
        )


if __name__ == "__main__":
    unittest.main()
