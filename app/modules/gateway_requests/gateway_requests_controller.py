import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_gateway_requests_service
from app.core.responses import BaseAPIResponse
from app.modules.analytics.analytics_service import TIME_RANGE_MAP
from app.modules.gateway_requests.gateway_requests_schema import (
    GatewayRequestLogCreate,
    GatewayRequestLogResponse,
    GatewayRequestLogsPaginatedResponse,
    GatewayRequestLogUpdate,
)
from app.modules.gateway_requests.gateway_requests_service import GatewayRequestsService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/gateway-requests", tags=["Gateway Requests"])


@api_v1_router.post(
    "/",
    response_model=BaseAPIResponse[GatewayRequestLogResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_gateway_request_log(
    request_data: GatewayRequestLogCreate,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Create a new gateway request log."""
    try:
        request_log = gateway_requests_service.log_request(request_data)
        return BaseAPIResponse.success_response(
            GatewayRequestLogResponse.model_validate(request_log)
        )
    except Exception as e:
        logger.exception("Failed to create gateway request log")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the gateway request log.",
        ) from e


@api_v1_router.get(
    "/",
    response_model=BaseAPIResponse[GatewayRequestLogsPaginatedResponse],
)
def get_gateway_request_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: str | None = Query(None),
    user_id: str | None = Query(None),
    routing_decision: str | None = Query(None),
    provider: str | None = Query(None),
    model: str | None = Query(None),
    search: str | None = Query(None),
    min_latency_ms: int | None = Query(None),
    max_latency_ms: int | None = Query(None),
    evaluation_status: str | None = Query(None),
    is_false_positive: bool | None = Query(None),
    has_error: bool | None = Query(None),
    time_range: str | None = Query(
        None,
        description="Time range shortcut: 1h, 3h, 6h, 12h, 1d, 3d, 7d, 14d, 30d",
    ),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Fetch a filtered, paginated list of gateway request traces."""
    try:
        if time_range and time_range.lower() in TIME_RANGE_MAP:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - TIME_RANGE_MAP[time_range.lower()]
        elif start_time and start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time and end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        logs, total = gateway_requests_service.get_request_logs(
            skip=skip,
            limit=limit,
            tenant_id=tenant_id,
            user_id=user_id,
            routing_decision=routing_decision,
            provider=provider,
            model=model,
            search=search,
            min_latency_ms=min_latency_ms,
            max_latency_ms=max_latency_ms,
            evaluation_status=evaluation_status,
            is_false_positive=is_false_positive,
            has_error=has_error,
            start_time=start_time,
            end_time=end_time,
        )
        return BaseAPIResponse.success_response(
            GatewayRequestLogsPaginatedResponse(
                items=[GatewayRequestLogResponse.model_validate(log) for log in logs],
                total=total,
                skip=skip,
                limit=limit,
            )
        )
    except Exception as e:
        logger.exception("Failed to fetch gateway request logs")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching the gateway request logs.",
        ) from e


@api_v1_router.get(
    "/pending-evaluations",
    response_model=BaseAPIResponse[list[GatewayRequestLogResponse]],
)
def get_pending_evaluations(
    limit: int = 50,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Fetch pending requests for background evaluation."""
    try:
        logs = gateway_requests_service.get_pending_evaluations(limit=limit)
        return BaseAPIResponse.success_response(
            [GatewayRequestLogResponse.model_validate(log) for log in logs]
        )
    except Exception as e:
        logger.exception("Failed to fetch pending evaluations")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching pending evaluations.",
        ) from e


@api_v1_router.put(
    "/{log_id}",
    response_model=BaseAPIResponse[GatewayRequestLogResponse],
)
def update_gateway_request_log(
    log_id: int,
    update_data: GatewayRequestLogUpdate,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Update a log after evaluation is complete."""
    try:
        updated_log = gateway_requests_service.update_evaluation(log_id, update_data)
        if not updated_log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Gateway request log with ID {log_id} not found.",
            )
        return BaseAPIResponse.success_response(
            GatewayRequestLogResponse.model_validate(updated_log)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update gateway request log with ID {log_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating the gateway request log.",
        ) from e


@api_v1_router.get(
    "/{log_id}",
    response_model=BaseAPIResponse[GatewayRequestLogResponse],
)
def get_gateway_request_log(
    log_id: int,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Fetch a specific gateway request log by ID."""
    try:
        log = gateway_requests_service.get_request_log(log_id)
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Gateway request log with ID {log_id} not found.",
            )
        return BaseAPIResponse.success_response(
            GatewayRequestLogResponse.model_validate(log)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to fetch gateway request log with ID {log_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching the gateway request log.",
        ) from e


@api_v1_router.get(
    "/by-hash/{exact_hash}",
    response_model=BaseAPIResponse[GatewayRequestLogResponse],
)
def get_gateway_request_log_by_hash(
    exact_hash: str,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Fetch a specific gateway request log by exact hash."""
    try:
        log = gateway_requests_service.get_request_log_by_hash(exact_hash)
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Gateway request log with hash {exact_hash} not found.",
            )
        return BaseAPIResponse.success_response(
            GatewayRequestLogResponse.model_validate(log)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to fetch gateway request log with hash {exact_hash}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching the gateway request log.",
        ) from e


@api_v1_router.delete(
    "/{log_id}",
    response_model=BaseAPIResponse[None],
)
def delete_gateway_request_log(
    log_id: int,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Delete an existing gateway request log."""
    try:
        success = gateway_requests_service.delete_request_log(log_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Gateway request log with ID {log_id} not found.",
            )
        return BaseAPIResponse.success_response(None)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete gateway request log with ID {log_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the gateway request log.",
        ) from e
