import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.guards import api_key_auth_guard
from app.core.responses import BaseAPIResponse
from app.core.time_ranges import resolve_time_range
from app.modules.shadow_cache.shadow_cache_schema import (
    ShadowCacheEvaluationListResponse,
    ShadowCacheSummaryResponse,
    ShadowMatchType,
)
from app.modules.shadow_cache.shadow_cache_service import ShadowCacheService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/shadow-cache", tags=["Shadow Cache"])


@api_v1_router.get(
    "/summary",
    response_model=BaseAPIResponse[ShadowCacheSummaryResponse],
    dependencies=[Depends(api_key_auth_guard)],
)
def get_shadow_cache_summary(
    time_range: str | None = Query(
        None,
        description="Time range shortcut: 1h, 3h, 6h, 12h, 1d, 3d, 7d, 14d, 30d",
    ),
    start_time: datetime | None = Query(
        None, description="Start time for the shadow window"
    ),
    end_time: datetime | None = Query(
        None, description="End time for the shadow window"
    ),
    tenant_id: str | None = Query(None, description="Optional tenant ID filter"),
    db_read: Session = Depends(get_db),
):
    """Shadow-pipeline readiness: would-be hit rate, equivalence rate, and savings.

    Backs the cache-readiness indicator: when ``readiness.status`` is ``READY``
    the observed traffic supports switching ``CACHE_MODE`` from ``shadow`` to
    ``active``.
    """
    resolved_start, resolved_end, resolved_range = resolve_time_range(
        time_range=time_range, start_time=start_time, end_time=end_time
    )
    service = ShadowCacheService(db_read)
    try:
        data = service.get_summary(
            start_time=resolved_start,
            end_time=resolved_end,
            tenant_id=tenant_id,
            time_range=resolved_range,
        )
        return BaseAPIResponse.success_response(data)
    except Exception:
        logger.exception("Failed to build shadow cache summary")
        raise


@api_v1_router.get(
    "/evaluations",
    response_model=BaseAPIResponse[ShadowCacheEvaluationListResponse],
    dependencies=[Depends(api_key_auth_guard)],
)
def list_shadow_cache_evaluations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    tenant_id: str | None = Query(None, description="Optional tenant ID filter"),
    would_have_hit: bool | None = Query(
        None, description="Only evaluations that would (not) have been cache hits"
    ),
    match_type: ShadowMatchType | None = Query(
        None, description="Filter by the cache layer that would have answered"
    ),
    is_equivalent: bool | None = Query(
        None, description="Filter by judged equivalence of the cached answer"
    ),
    model: str | None = Query(None, description="Filter by model"),
    intent: str | None = Query(None, description="Filter by classified intent"),
    start_time: datetime | None = Query(None, description="Start time"),
    end_time: datetime | None = Query(None, description="End time"),
    db_read: Session = Depends(get_db),
):
    """Paginated shadow evaluations, newest first, for drill-down inspection."""
    resolved_start, resolved_end, _ = resolve_time_range(
        time_range=None, start_time=start_time, end_time=end_time
    )
    service = ShadowCacheService(db_read)
    try:
        items, total = service.get_evaluations(
            skip=skip,
            limit=limit,
            tenant_id=tenant_id,
            would_have_hit=would_have_hit,
            match_type=match_type.value if match_type else None,
            is_equivalent=is_equivalent,
            model=model,
            intent=intent,
            start_time=resolved_start if start_time else None,
            end_time=resolved_end if (start_time or end_time) else None,
        )
        return BaseAPIResponse.success_response(
            ShadowCacheEvaluationListResponse(
                items=items, total=total, skip=skip, limit=limit
            )
        )
    except Exception:
        logger.exception("Failed to list shadow cache evaluations")
        raise
