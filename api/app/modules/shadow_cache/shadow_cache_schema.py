from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ShadowMatchType(str, Enum):
    """The cache layer that would have answered, mirroring ``RoutingDecision``."""

    REDIS_EXACT = "REDIS_EXACT"
    QDRANT_EXACT = "QDRANT_EXACT"
    QDRANT_FAST = "QDRANT_FAST"
    QDRANT_SEMANTIC = "QDRANT_SEMANTIC"
    MISS = "MISS"


class ReadinessStatus(str, Enum):
    """Whether the shadow telemetry supports switching to ``CACHE_MODE=active``."""

    READY = "READY"
    WARMING_UP = "WARMING_UP"
    NOT_READY = "NOT_READY"


class ShadowCacheEvaluationCreate(BaseModel):
    request_id: int | None = None
    exact_hash: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    model: str | None = None
    provider: str | None = None
    would_have_hit: bool
    match_type: ShadowMatchType
    point_id: str | None = None
    match_score: float | None = None
    rerank_score: float | None = None
    score_breakdown: dict[str, Any] | None = None
    cached_response_text: str | None = None
    actual_response_text: str | None = None
    is_equivalent: bool | None = None
    intent: str | None = None
    entities: list[str] | None = None
    latency_breakdown: dict[str, Any] | None = None
    provider_latency_ms: int | None = None
    cache_lookup_latency_ms: int | None = None
    projected_latency_saved_ms: int | None = None


class ShadowCacheEvaluationResponse(ShadowCacheEvaluationCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShadowCacheEvaluationListResponse(BaseModel):
    items: list[ShadowCacheEvaluationResponse]
    total: int
    skip: int
    limit: int


class ShadowMatchTypeBreakdown(BaseModel):
    match_type: ShadowMatchType
    count: int


class ShadowIntentBreakdown(BaseModel):
    intent: str
    evaluations: int
    would_have_hits: int
    hit_rate: float


class ShadowModelBreakdown(BaseModel):
    model: str
    provider: str | None = None
    evaluations: int
    would_have_hits: int
    hit_rate: float


class ShadowReadiness(BaseModel):
    status: ReadinessStatus
    reasons: list[str] = Field(default_factory=list)
    min_evaluations: int
    min_hit_rate: float
    min_equivalence_rate: float


class ShadowCacheSummaryResponse(BaseModel):
    """Operator-facing view of the shadow pipeline over a time window."""

    cache_mode: str
    start_time: datetime
    end_time: datetime
    time_range: str | None = None

    total_evaluations: int
    would_have_hits: int
    hit_rate: float
    match_type_breakdown: list[ShadowMatchTypeBreakdown]

    compared_evaluations: int
    equivalent_evaluations: int
    non_equivalent_evaluations: int
    equivalence_rate: float | None

    avg_latency_saved_ms: float | None = None
    projected_latency_saved_ms: int | None = None
    avg_cache_lookup_ms: float | None = None
    avg_provider_ms: float | None = None

    by_intent: list[ShadowIntentBreakdown]
    by_model: list[ShadowModelBreakdown]
    readiness: ShadowReadiness
