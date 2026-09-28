from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.core.database import Base


class LlmQuotaRule(Base):
    __tablename__ = "llm_quota_rules"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    provider = Column(String(50), nullable=True, index=True)  # e.g. openai, ollama
    model = Column(String(100), nullable=True, index=True)  # e.g. gpt-4
    user_id = Column(String(100), nullable=True, index=True)
    tenant_id = Column(String(100), nullable=True, index=True)

    max_rpm = Column(Integer, nullable=True)  # Requests Per Minute
    max_tpm = Column(Integer, nullable=True)  # Tokens Per Minute

    is_active = Column(Boolean, default=True)
