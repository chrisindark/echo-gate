from datetime import UTC, datetime

from sqlalchemy import (
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


class CacheEvaluation(Base):
    """Ground-truth audit store for sampled semantic cache hits.

    Captures unbiased real-world comparisons between cached answers and fresh
    provider completions. Populated asynchronously out of band without affecting
    client latency.
    """

    __tablename__ = "cache_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )

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
    match_type = Column(String(50), nullable=True, index=True)

    query_text = Column(Text, nullable=True)
    cached_answer = Column(Text, nullable=True)
    fresh_answer = Column(Text, nullable=True)

    intent = Column(String(100), index=True, nullable=True)
    vector_similarity = Column(Numeric(precision=6, scale=5), nullable=True)
    rerank_score = Column(Numeric(precision=6, scale=5), nullable=True)

    # Equivalence judged by JudgeService (True = equivalent, False = false positive / poisoned)
    is_equivalent = Column(Boolean, nullable=True, index=True)

    point_id = Column(String(36), index=True, nullable=True)
    latency_saved_ms = Column(Integer, nullable=True)

    # Set to True if instant eviction (kill switch) purged the point from Qdrant/Redis
    evicted = Column(Boolean, default=False, nullable=False, index=True)
