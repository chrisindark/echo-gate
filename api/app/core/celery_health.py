"""Celery Worker Health Probe for Kubernetes liveness/readiness probes.

Usage in Kubernetes Pod spec:
  livenessProbe:
    exec:
      command: ["python", "-m", "app.core.celery_health"]
    initialDelaySeconds: 15
    periodSeconds: 10
    timeoutSeconds: 2
"""

import logging
import sys

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def check_worker_broker_connection(timeout: float = 1.0) -> bool:
    """Verify that the worker can establish and ping its message broker."""
    try:
        with celery_app.connection_for_read() as conn:
            conn.ensure_connection(max_retries=1, timeout=timeout)
        return True
    except Exception as e:
        logger.error("Celery worker broker probe failed: %s", e)
        return False


def main() -> int:
    is_healthy = check_worker_broker_connection(timeout=1.0)
    if is_healthy:
        print('{"status": "healthy", "service": "celery_worker"}')
        return 0
    else:
        print('{"status": "unhealthy", "service": "celery_worker"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
