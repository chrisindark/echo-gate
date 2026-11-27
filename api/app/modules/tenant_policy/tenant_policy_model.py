"""SQLAlchemy model for admin-managed per-tenant privacy & cache policies."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.core.database import Base


class TenantPrivacyPolicy(Base):
    """Per-tenant restrictions for caching, zero-retention, PII scrubbing, and guardrails.

    Managed exclusively by operators via ``X-Admin-Key`` endpoints. Row-Level
    Security restricts direct non-system reads to the active tenant context.
    """

    __tablename__ = "tenant_privacy_policies"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tenant_id = Column(String(100), nullable=False, unique=True, index=True)

    # When False, semantic & exact caching (Redis + Qdrant) are completely
    # disabled for this tenant regardless of caller cache_enabled directives.
    semantic_cache_enabled = Column(Boolean, nullable=False, default=True)

    # When True, enforces Zero-Retention Mode (suppresses Postgres logs,
    # conversation turn storage, and Redis/Qdrant cache reads & writes).
    zero_retention = Column(Boolean, nullable=False, default=False)

    # "inherit" | "enforced" | "disabled"
    pii_scrubbing_mode = Column(String(20), nullable=False, default="inherit")

    # "inherit" | "off" | "inject" | "block"
    guardrails_mode = Column(String(20), nullable=False, default="inherit")

    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
