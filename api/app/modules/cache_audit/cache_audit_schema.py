from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CacheAuditCreate(BaseModel):
    request_id: int | None = None
    exact_hash: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    model: str | None = None
    provider: str | None = None
    match_type: str | None = "qdrant_semantic_path"
    query_text: str | None = None
    cached_answer: str | None = None
    fresh_answer: str | None = None
    intent: str | None = None
    vector_similarity: float | Decimal | None = None
    rerank_score: float | Decimal | None = None
    is_equivalent: bool | None = None
    point_id: str | None = None
    latency_saved_ms: int | None = None
    evicted: bool = False


class CacheAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    request_id: int | None = None
    exact_hash: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    model: str | None = None
    provider: str | None = None
    match_type: str | None = None
    query_text: str | None = None
    cached_answer: str | None = None
    fresh_answer: str | None = None
    intent: str | None = None
    vector_similarity: float | None = None
    rerank_score: float | None = None
    is_equivalent: bool | None = None
    point_id: str | None = None
    latency_saved_ms: int | None = None
    evicted: bool = False


class CacheAuditSummary(BaseModel):
    total_audits: int = 0
    equivalent_count: int = 0
    non_equivalent_count: int = 0
    uncompared_count: int = 0
    equivalence_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    evicted_count: int = 0


class IntentThresholdOverride(BaseModel):
    """Calibrated threshold override stored in Redis at cache:thresholds:{intent}."""

    intent: str
    high: float = Field(..., ge=0.5, le=1.0)
    moderate: float = Field(..., ge=0.5, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    alpha: float = Field(default=0.02, ge=0.0, le=1.0)
    calibrated_at: datetime


class IntentCalibrationResult(BaseModel):
    """Result of conformal risk control calibration for an intent."""

    intent: str
    high: float
    moderate: float
    sample_count: int
    error_count: int
    empirical_error_rate: float
    target_alpha: float
    calibrated: bool
    reason: str | None = None
    calibrated_at: datetime
    applied_to_redis: bool = False
