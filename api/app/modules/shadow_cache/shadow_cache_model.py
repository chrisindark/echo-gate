from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)

from app.core.database import Base


class ShadowCacheEvaluation(Base):
    """What the cache would have done for a request served by the provider.

    Written only in ``CACHE_MODE=shadow``: the serving path always calls the
    provider, and a background task replays the full cache pipeline to record
    whether a match would have occurred, which layer matched, how confident it
    was, and whether the cached answer was equivalent to the fresh one.
    """

    __tablename__ = "shadow_cache_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )

    # Nullable: the gateway request log is written by a sibling background task,
    # so this link is filled in when that task has already completed. Join on
    # exact_hash when it is absent.
    request_id = Column(
        Integer,
        ForeignKey("gateway_requests.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    exact_hash = Column(String(64), index=True, nullable=True)
    tenant_id = Column(String(100), index=True, nullable=True)
    user_id = Column(String(100), index=True, nullable=True)
    model = Column(String(100), index=True, nullable=True)
    provider = Column(String(100), nullable=True)

    would_have_hit = Column(Boolean, nullable=False, default=False, index=True)
    # REDIS_EXACT | QDRANT_EXACT | QDRANT_FAST | QDRANT_SEMANTIC | MISS
    match_type = Column(String(50), nullable=False, index=True)
    point_id = Column(String(36), index=True, nullable=True)
    match_score = Column(Numeric(precision=6, scale=5), nullable=True)
    rerank_score = Column(Numeric(precision=6, scale=5), nullable=True)
    score_breakdown = Column(JSON, nullable=True)

    cached_response_text = Column(Text, nullable=True)
    actual_response_text = Column(Text, nullable=True)
    # Populated when the shadow match was compared against the fresh answer;
    # NULL when no match occurred or the comparison was skipped/failed.
    is_equivalent = Column(Boolean, nullable=True, index=True)

    intent = Column(String(100), index=True, nullable=True)
    entities = Column(JSON, nullable=True)
    latency_breakdown = Column(JSON, nullable=True)
    provider_latency_ms = Column(Integer, nullable=True)
    cache_lookup_latency_ms = Column(Integer, nullable=True)
    # Provider time the cache hit would have avoided (provider - cache lookup).
    projected_latency_saved_ms = Column(Integer, nullable=True)
