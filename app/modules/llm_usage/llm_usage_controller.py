import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db, get_db_read
from app.modules.llm_usage.llm_usage_schema import (LlmUsageLogCreate,
                                                    LlmUsageLogResponse,
                                                    LlmUsageLogUpdate)
from app.modules.llm_usage.llm_usage_service import LlmUsageService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/llm-usage", tags=["Llm-usage"])


@api_v1_router.get("/", response_model=list[LlmUsageLogResponse])
def list_llm_usage_logs(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db_read),
):
    """
    List LLM usage logs with optional time range filters and pagination.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    return llm_usage_service.get_llm_usage_logs(
        start_time=start_time, end_time=end_time, skip=skip, limit=limit
    )


@api_v1_router.get("/{log_id}", response_model=LlmUsageLogResponse)
def get_llm_usage_log(
    log_id: int, db: Session = Depends(get_db), db_read: Session = Depends(get_db_read)
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
    return log


@api_v1_router.post(
    "/", response_model=LlmUsageLogResponse, status_code=status.HTTP_201_CREATED
)
def create_llm_usage_log(
    log_data: LlmUsageLogCreate,
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db_read),
):
    """
    Create a new LLM usage log.
    """
    llm_usage_service = LlmUsageService(db, db_read)
    try:
        return llm_usage_service.create_llm_usage_log(log_data)
    except Exception as e:
        logger.error(f"Error creating LLM usage log: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create LLM usage log",
        )


@api_v1_router.put("/{log_id}", response_model=LlmUsageLogResponse)
def update_llm_usage_log(
    log_id: int,
    log_data: LlmUsageLogUpdate,
    db: Session = Depends(get_db),
    db_read: Session = Depends(get_db_read),
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
        return updated_log
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating LLM usage log {log_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update LLM usage log",
        )


@api_v1_router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_llm_usage_log(
    log_id: int, db: Session = Depends(get_db), db_read: Session = Depends(get_db_read)
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting LLM usage log {log_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete LLM usage log",
        )
