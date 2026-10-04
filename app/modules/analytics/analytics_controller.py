import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db_read
from app.core.responses import BaseAPIResponse
from app.modules.analytics.analytics_schema import AnalyticsDashboardResponse
from app.modules.analytics.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


@api_v1_router.get(
    "/dashboard",
    response_model=BaseAPIResponse[AnalyticsDashboardResponse],
)
def get_dashboard_data(
    start_time: datetime | None = Query(
        None, description="Start time for analytics range"
    ),
    end_time: datetime | None = Query(None, description="End time for analytics range"),
    tenant_id: str | None = Query(None, description="Optional tenant ID filter"),
    db_read: Session = Depends(get_db_read),
):
    """
    Get aggregated analytics data for the dashboard (KPIs, time series, cache performance, and model breakdown).
    """
    analytics_service = AnalyticsService(db_read)
    try:
        data = analytics_service.get_dashboard_data(
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
        )
        return BaseAPIResponse.success_response(data)
    except Exception:
        logger.exception("Failed to get analytics dashboard data")
        raise
