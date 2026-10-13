from fastapi import APIRouter
from fastapi_health import health

from app.core.config import config
from app.core.responses import BaseAPIResponse
from app.modules.health.health_service import (
    celery_failure_handler,
    celery_success_handler,
    check_celery_broker,
    check_celery_workers,
    check_database,
    check_liveness,
    check_redis,
    check_vector_db,
    liveness_failure_handler,
    liveness_success_handler,
    readiness_failure_handler,
    readiness_success_handler,
)

router = APIRouter(prefix="/api/v1/health", tags=["Health"])


@router.get("", response_model=BaseAPIResponse[dict])
def health_check():
    """Basic health check endpoint returning API status."""
    return BaseAPIResponse.success_response(
        response={
            "status": "healthy",
            "version": config.VERSION,
        }
    )


router.add_api_route(
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

router.add_api_route(
    "/ready",
    health(
        [check_database, check_redis, check_vector_db],
        success_handler=readiness_success_handler,
        failure_handler=readiness_failure_handler,
        success_status=200,
        failure_status=503,
    ),
    methods=["GET"],
    summary="Readiness probe for Redis, Database, and Vector DB with 1s timeouts",
)

router.add_api_route(
    "/worker",
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

router.add_api_route(
    "/celery",
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
