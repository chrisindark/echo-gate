import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import get_gateway_requests_service
from app.core.responses import BaseAPIResponse
from app.modules.gateway_requests.gateway_requests_schema import (
    GatewayRequestLogCreate,
    GatewayRequestLogResponse,
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
    response_model=BaseAPIResponse[list[GatewayRequestLogResponse]],
)
def get_gateway_request_logs(
    skip: int = 0,
    limit: int = 100,
    tenant_id: str | None = None,
    user_id: str | None = None,
    gateway_requests_service: GatewayRequestsService = Depends(
        get_gateway_requests_service
    ),
) -> dict:
    """Fetch a paginated list of gateway request logs."""
    try:
        logs = gateway_requests_service.get_request_logs(
            skip=skip, limit=limit, tenant_id=tenant_id, user_id=user_id
        )
        return BaseAPIResponse.success_response(
            [GatewayRequestLogResponse.model_validate(log) for log in logs]
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
