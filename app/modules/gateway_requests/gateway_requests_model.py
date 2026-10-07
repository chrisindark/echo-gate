from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, Numeric, String, Text

from app.core.database import Base


class GatewayRequestLog(Base):
    __tablename__ = "gateway_requests"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    query_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=True)

    routing_decision = Column(
        String(50), index=True, nullable=False
    )  # e.g., 'LLM', 'REDIS_EXACT', 'QDRANT_EXACT', 'QDRANT_SEMANTIC'
    provider = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)

    point_id = Column(String(36), index=True, nullable=True)  # UUID in Qdrant
    exact_hash = Column(String(64), index=True, nullable=True)

    intent = Column(String(100), index=True, nullable=True)
    core_operation = Column(String(255), index=True, nullable=True)
    core_subject = Column(String(255), index=True, nullable=True)
    subject_modifier = Column(String(255), index=True, nullable=True)
    action_modifier = Column(String(255), index=True, nullable=True)

    rerank_score = Column(Numeric(precision=5, scale=4), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    provider_latency_ms = Column(Integer, nullable=True, index=True)
    cache_lookup_latency_ms = Column(Integer, nullable=True)
    latency_breakdown = Column(JSON, nullable=True)

    user_id = Column(String(100), index=True, nullable=True)
    tenant_id = Column(String(100), index=True, nullable=True)
    session_id = Column(String(100), index=True, nullable=True)
    conversation_id = Column(String(100), index=True, nullable=True)

    # Evaluation Fields
    evaluation_status = Column(
        String(20), default="PENDING", index=True
    )  # 'PENDING', 'IN_PROGRESS', 'EVALUATED', 'SKIPPED'
    llm_relevance_score = Column(Numeric(precision=5, scale=4), nullable=True)
    llm_contradiction_score = Column(Numeric(precision=5, scale=4), nullable=True)
    llm_instruction_score = Column(Numeric(precision=5, scale=4), nullable=True)
    is_false_positive = Column(Boolean, nullable=True, index=True)

    error_message = Column(Text, nullable=True)
