from fastapi import APIRouter
from fastapi_health import health

from app.core.config import config
from app.core.responses import BaseAPIResponse
from app.modules.health.health_service import (
    DEPENDENCY_CHECKS,
    READINESS_CHECKS,
    celery_failure_handler,
    celery_success_handler,
    check_celery_broker,
    check_celery_workers,
    check_liveness,
    dependencies_report_handler,
    liveness_failure_handler,
    liveness_success_handler,
    readiness_failure_handler,
    readiness_success_handler,
)

router = APIRouter(prefix="/api/v1/health", tags=["Health"])
root_router = APIRouter(tags=["Health"])


@router.get("", response_model=BaseAPIResponse[dict])
def health_check():
    """Basic health check endpoint returning API status."""
    return BaseAPIResponse.success_response(
        response={
            "status": "healthy",
            "version": config.VERSION,
        }
    )


for r in (router, root_router):
    r.add_api_route(
        "/live",
        health(
            [check_liveness],
            success_handler=liveness_success_handler,
            failure_handler=liveness_failure_handler,
            success_status=200,
            failure_status=503,
        ),
        methods=["GET"],
        summary="Process liveness probe",
    )

    r.add_api_route(
        "/ready",
        health(
            list(READINESS_CHECKS),
            success_handler=readiness_success_handler,
            failure_handler=readiness_failure_handler,
            success_status=200,
            failure_status=503,
        ),
        methods=["GET"],
        summary=(
            "Readiness probe: gates on Postgres only, with a 1s timeout. Redis and "
            "Qdrant are degradable (a failure is a cache miss, not a wrong answer) "
            "and are reported on /health/deps instead, so a cache blip does not "
            "evict every replica"
        ),
    )

root_router.add_api_route(
    "/health/deps",
    health(
        list(DEPENDENCY_CHECKS),
        success_handler=dependencies_report_handler,
        failure_handler=dependencies_report_handler,
        success_status=200,
        failure_status=200,
    ),
    methods=["GET"],
    summary="Non-gating dependency report alias for alerting",
)

router.add_api_route(
    "/deps",
    health(
        list(DEPENDENCY_CHECKS),
        success_handler=dependencies_report_handler,
        failure_handler=dependencies_report_handler,
        success_status=200,
        # Deliberately 200: this route is an alerting surface, never a gate.
        failure_status=200,
    ),
    methods=["GET"],
    summary=(
        "Non-gating dependency report for alerting: Postgres, Redis, Qdrant, "
        "Celery broker and workers (1s timeouts). Always returns 200; read the "
        "status field (healthy / degraded / unhealthy) instead of the HTTP code"
    ),
)

for r in (router, root_router):
    prefix = "/health" if r is root_router else ""
    r.add_api_route(
        f"{prefix}/worker",
        health(
            [check_celery_broker, check_celery_workers],
            success_handler=celery_success_handler,
            failure_handler=celery_failure_handler,
            success_status=200,
            failure_status=503,
        ),
        methods=["GET"],
        summary="Celery worker and broker readiness probe",
    )

    r.add_api_route(
        f"{prefix}/celery",
        health(
            [check_celery_broker, check_celery_workers],
            success_handler=celery_success_handler,
            failure_handler=celery_failure_handler,
            success_status=200,
            failure_status=503,
        ),
        methods=["GET"],
        summary="Celery worker and broker readiness probe alias",
    )
