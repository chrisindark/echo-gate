import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import BaseAPIResponse
from app.modules.llm_usage.llm_usage_schema import (
    LlmUsageLogCreate,
    LlmUsageLogResponse,
    LlmUsageLogUpdate,
)
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.modules.llm_usage.tasks import dispatch_process_uncalculated_costs

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/llm-usage", tags=["Llm-usage"])


@api_v1_router.get("/", response_model=BaseAPIResponse[list[LlmUsageLogResponse]])
def list_llm_usage_logs(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db),
):
    """
    List LLM usage logs with optional time range filters and pagination.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    result = llm_usage_service.get_llm_usage_logs(
        start_time=start_time, end_time=end_time, skip=skip, limit=limit
    )
    return BaseAPIResponse.success_response(result)


@api_v1_router.get("/{log_id}", response_model=BaseAPIResponse[LlmUsageLogResponse])
def get_llm_usage_log(
    log_id: int, db: Session = Depends(get_db), db_read: Session = Depends(get_db)
):
    """
    Get a specific LLM usage log by ID.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    log = llm_usage_service.get_llm_usage_log(log_id)
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="LLM usage log not found"
        )
    return BaseAPIResponse.success_response(log)


@api_v1_router.post(
    "/",
    response_model=BaseAPIResponse[LlmUsageLogResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_llm_usage_log(
    log_data: LlmUsageLogCreate,
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db),
):
    """
    Create a new LLM usage log.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    try:
        result = llm_usage_service.create_llm_usage_log(log_data)
        return BaseAPIResponse.success_response(
            result, status_code=status.HTTP_201_CREATED
        )
    except Exception as e:
        logger.error(f"Error creating LLM usage log: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create LLM usage log",
        )


@api_v1_router.put("/{log_id}", response_model=BaseAPIResponse[LlmUsageLogResponse])
def update_llm_usage_log(
    log_id: int,
    log_data: LlmUsageLogUpdate,
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db),
):
    """
    Update an existing LLM usage log.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    try:
        updated_log = llm_usage_service.update_llm_usage_log(log_id, log_data)
        if not updated_log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="LLM usage log not found"
            )
        return BaseAPIResponse.success_response(updated_log)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating LLM usage log {log_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update LLM usage log",
        )


@api_v1_router.delete(
    "/{log_id}", response_model=BaseAPIResponse[None], status_code=status.HTTP_200_OK
)
def delete_llm_usage_log(
    log_id: int, db: Session = Depends(get_db), db_read: Session = Depends(get_db)
):
    """
    Delete an existing LLM usage log.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    try:
        success = llm_usage_service.delete_llm_usage_log(log_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="LLM usage log not found"
            )
        return BaseAPIResponse.success_response(None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting LLM usage log {log_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete LLM usage log",
        )


@api_v1_router.post(
    "/calculate-costs",
    response_model=BaseAPIResponse[dict],
)
def trigger_cost_calculation(
    background_tasks: BackgroundTasks,
) -> dict:
    """Trigger background cost calculation with fallback to FastAPI background tasks."""
    task_id, dispatched_to_celery = dispatch_process_uncalculated_costs(
        background_tasks=background_tasks
    )
    return BaseAPIResponse.success_response(
        {
            "task_id": task_id,
            "dispatched_to_celery": dispatched_to_celery,
            "message": "Cost calculation queued successfully"
            + (" via Celery" if dispatched_to_celery else " in background worker"),
        }
    )
