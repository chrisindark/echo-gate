import asyncio
import logging
from typing import Any

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.dependencies import DependencyContainer

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 1.0


async def check_liveness() -> bool:
    """Process liveness probe."""
    return True


async def check_database() -> bool:
    """Relational Database probe with strict 1s timeout."""
    try:

        def _ping():
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))

        await asyncio.wait_for(asyncio.to_thread(_ping), timeout=PROBE_TIMEOUT_SECONDS)
        return True
    except Exception as e:
        logger.error("Health check failed for Database: %s", e)
        return False


async def check_redis() -> bool:
    """Redis cache probe with strict 1s timeout."""
    try:
        redis_service = await DependencyContainer.get_redis_service()
        res = await asyncio.wait_for(
            redis_service.redis.ping(), timeout=PROBE_TIMEOUT_SECONDS
        )
        return bool(res)
    except Exception as e:
        logger.error("Health check failed for Redis: %s", e)
        return False


async def check_vector_db() -> bool:
    """Qdrant vector database probe with strict 1s timeout."""
    try:
        qdrant_service = DependencyContainer.get_qdrant_client_service()
        await asyncio.wait_for(
            asyncio.to_thread(qdrant_service.get_collections),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return True
    except Exception as e:
        logger.error("Health check failed for Vector DB (Qdrant): %s", e)
        return False


async def check_celery_broker() -> bool:
    """Celery broker connectivity probe with strict 1s timeout."""
    try:
        from app.worker.celery_app import celery_app

        def _ping_broker():
            with celery_app.connection_for_read() as conn:
                conn.ensure_connection(max_retries=1, timeout=PROBE_TIMEOUT_SECONDS)

        await asyncio.wait_for(
            asyncio.to_thread(_ping_broker), timeout=PROBE_TIMEOUT_SECONDS
        )
        return True
    except Exception as e:
        logger.error("Health check failed for Celery Broker: %s", e)
        return False


async def check_celery_workers() -> bool:
    """Celery active worker ping probe with strict 1s timeout."""
    try:
        from app.worker.celery_app import celery_app

        def _ping_workers():
            res = celery_app.control.ping(timeout=PROBE_TIMEOUT_SECONDS)
            return bool(res and len(res) > 0)

        return await asyncio.wait_for(
            asyncio.to_thread(_ping_workers), timeout=PROBE_TIMEOUT_SECONDS
        )
    except Exception as e:
        logger.error("Health check failed for Celery Workers: %s", e)
        return False


async def liveness_success_handler(**kwargs: Any) -> dict[str, Any]:
    return {"status": "alive"}


async def liveness_failure_handler(**kwargs: Any) -> dict[str, Any]:
    return {"status": "unhealthy"}


async def readiness_success_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "ready",
        "checks": {
            "database": "ok",
            "redis": "ok",
            "vector_db": "ok",
        },
    }


async def readiness_failure_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "unhealthy",
        "checks": {
            "database": "ok" if kwargs.get("check_database") else "failed",
            "redis": "ok" if kwargs.get("check_redis") else "failed",
            "vector_db": "ok" if kwargs.get("check_vector_db") else "failed",
        },
    }


async def celery_success_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "ready",
        "checks": {
            "celery_broker": "ok",
            "celery_workers": "ok",
        },
    }


async def celery_failure_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "unhealthy",
        "checks": {
            "celery_broker": "ok" if kwargs.get("check_celery_broker") else "failed",
            "celery_workers": "ok" if kwargs.get("check_celery_workers") else "failed",
        },
    }
