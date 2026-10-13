import unittest

from starlette.testclient import TestClient

from app.main import app
from app.modules.health.health_service import (
    check_celery_broker,
    check_celery_workers,
    check_database,
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
        """Verify /health/live returns HTTP 200 with alive status."""
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    def test_health_ready_all_healthy(self):
        """Verify /health/ready returns HTTP 200 when all dependencies are healthy."""
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_redis] = lambda: True
        app.dependency_overrides[check_vector_db] = lambda: True

        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(
            data["checks"],
            {"database": "ok", "redis": "ok", "vector_db": "ok"},
        )

    def test_health_ready_partial_failure(self):
        """Verify /health/ready returns HTTP 503 when any dependency fails."""
        app.dependency_overrides[check_database] = lambda: True
        app.dependency_overrides[check_redis] = lambda: False
        app.dependency_overrides[check_vector_db] = lambda: True

        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(
            data["checks"],
            {"database": "ok", "redis": "failed", "vector_db": "ok"},
        )

    def test_health_worker_all_healthy(self):
        """Verify /health/worker returns HTTP 200 when Celery broker and workers are healthy."""
        app.dependency_overrides[check_celery_broker] = lambda: True
        app.dependency_overrides[check_celery_workers] = lambda: True

        response = self.client.get("/health/worker")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(
            data["checks"],
            {"celery_broker": "ok", "celery_workers": "ok"},
        )

    def test_health_worker_broker_failure(self):
        """Verify /health/worker returns HTTP 503 when Celery broker fails."""
        app.dependency_overrides[check_celery_broker] = lambda: False
        app.dependency_overrides[check_celery_workers] = lambda: False

        response = self.client.get("/health/worker")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["status"], "unhealthy")
        self.assertEqual(
            data["checks"],
            {"celery_broker": "failed", "celery_workers": "failed"},
        )


if __name__ == "__main__":
    unittest.main()
