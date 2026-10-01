from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, Numeric, String, Text

from app.core.database import Base


class LlmUsageLog(Base):
    __tablename__ = "llm_usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    service_name = Column(
        String, index=True, nullable=False
    )  # e.g., 'openai', 'anthropic', 'google'
    model = Column(
        String, index=True, nullable=False
    )  # e.g., 'gpt-3.5-turbo', 'claude-3', 'gemini-2.0'
    caller_service_name = Column(
        String, index=True, nullable=False
    )  # e.g., 'fastapi microservice', 'nestjs microservice'

    user_id = Column(String(100), index=True, nullable=True)
    tenant_id = Column(String(100), index=True, nullable=True)
    session_id = Column(String(100), index=True, nullable=True)

    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(
        Integer, default=0
    )  # Tokens retrieved from prompt cache (discounted)
    cache_creation_tokens = Column(Integer, default=0)  # Tokens written to cache

    cost = Column(Numeric(precision=12, scale=6), default=0.000000)
    cost_calculated = Column(Boolean, default=False, server_default="0", index=True)

    latency_ms = Column(
        Integer, nullable=True
    )  # Round-trip execution time in milliseconds
    status_code = Column(
        Integer, nullable=True
    )  # HTTP status code (e.g., 200, 429, 500)
    is_success = Column(Boolean, default=True, index=True)
    error_message = Column(
        Text, nullable=True
    )  # Captured error details if the call failed
    finish_reason = Column(
        String(50), nullable=True
    )  # e.g., 'stop', 'length', 'content_filter'

    temperature = Column(Numeric(precision=3, scale=2), nullable=True)
    max_tokens = Column(Integer, nullable=True)

    input_text = Column(Text, nullable=True)
    output_text = Column(Text, nullable=True)

    extra_metadata = Column(JSON, nullable=True)
