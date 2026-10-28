import asyncio
import logging
from typing import Any

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.dependencies import DependencyContainer

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 1.0

# Only Postgres gates load-balancer membership.
#
# `/ready` used to fail when Redis, Qdrant, *or* Postgres failed. That conflated
# "I cannot serve a correct response" with "something I can degrade around is
# unavailable", so an orchestrator gating membership on it evicted every replica
# for a cache blip -- turning graceful degradation into an outage.
#
# What each dependency actually does to the request path when it is down:
#
# * Postgres -> NOT degradable. `LlmQuotaService.get_applicable_limits` reads
#   quota rules through `SessionLocal` with no handler, so the raised
#   `OperationalError` escapes `check_quota` as a 500 on every chat request. And
#   `ApiKeyAuthGuard` cannot resolve a credential at all:
#   `resolve_api_key` swallows the database error and returns `None`, which the
#   guard reports as 401. A keyed client with a perfectly valid key gets told it
#   is unauthenticated. That is a wrong answer, not a slow one.
# * Redis -> degradable. `check_quota` catches the failure and falls back to an
#   unlimited budget, and every cache read is wrapped so a miss just becomes a
#   provider call.
# * Qdrant -> degradable. Same: a failed lookup is logged and treated as a cache
#   miss, which the router answers from the provider.
# * Inference engine -> degradable at runtime. Every role it serves degrades the
#   same way (no candidates -> provider answer), so it must not evict replicas
#   either. It is *not* degradable at boot: `InferenceClient.verify()` refuses to
#   start the process, because otherwise every request would bypass the cache
#   while this probe reported healthy.
#
# So Redis, Qdrant and the engine stay on `/deps` for alerting, where a failure is
# worth paging on but must not pull a replica out of rotation.
GATING_DEPENDENCIES = frozenset({"database"})

# Maps the dependency kwargs `fastapi_health` passes to the handlers (keyed by
# the probe function's `__name__`) onto the names reported in the response body.
_CHECK_LABELS = {
    "check_database": "database",
    "check_redis": "redis",
    "check_vector_db": "vector_db",
    "check_celery_broker": "celery_broker",
    "check_celery_workers": "celery_workers",
    "check_inference": "inference",
}


def _check_status(kwargs: dict[str, Any]) -> dict[str, str]:
    """Render probe results as ``{name: "ok" | "failed"}``."""
    return {
        _CHECK_LABELS[key]: "ok" if value else "failed"
        for key, value in kwargs.items()
        if key in _CHECK_LABELS
    }


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


async def check_inference() -> bool:
    """Inference engine probe: reachable and serving every required role."""
    try:
        inference = DependencyContainer.get_inference_client()
        return await asyncio.wait_for(
            inference.healthy(), timeout=PROBE_TIMEOUT_SECONDS
        )
    except Exception as e:
        logger.error("Health check failed for Inference Engine: %s", e)
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
    return {"status": "healthy"}


async def liveness_failure_handler(**kwargs: Any) -> dict[str, Any]:
    return {"status": "unhealthy"}


async def readiness_success_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "healthy",
        "checks": _check_status(kwargs),
    }


async def readiness_failure_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "unhealthy",
        "checks": _check_status(kwargs),
    }


async def celery_success_handler(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "healthy",
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


async def dependencies_report_handler(**kwargs: Any) -> dict[str, Any]:
    """Render the full dependency roll-up for ``/health/deps``.

    Used as *both* the success and failure handler, with ``failure_status=200``,
    so this route never returns 503. That is the point: it is an observability
    surface, and a probe wired to it must not be able to take a replica out of
    rotation. Alerting reads ``status`` instead of the HTTP code.
    """
    checks = _check_status(kwargs)
    failed = sorted(name for name, state in checks.items() if state != "ok")

    if GATING_DEPENDENCIES.intersection(failed):
        overall = "unhealthy"
    elif failed:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "gating": sorted(GATING_DEPENDENCIES),
        "failed": failed,
        "checks": checks,
    }


# Probe sets, defined once the probes exist. `/ready` gates on Postgres alone;
# `/deps` reports everything so a Redis or Qdrant blip is still visible to an
# operator even though it no longer evicts this replica.
READINESS_CHECKS = (check_database,)
DEPENDENCY_CHECKS = (
    check_database,
    check_redis,
    check_vector_db,
    check_celery_broker,
    check_inference,
)
