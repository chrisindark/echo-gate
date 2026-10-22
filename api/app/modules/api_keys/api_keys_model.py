from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.core.database import Base


class ApiKey(Base):
    """Maps an API key to the tenant/user it authenticates as.

    This table is intentionally *not* under RLS: key resolution happens before a
    tenant context exists, so it must be readable regardless of the active
    tenant. Lookups are always by exact ``key_hash``.
    """

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    key_prefix = Column(String(16), index=True, nullable=False)
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    tenant_id = Column(String(100), index=True, nullable=False)
    user_id = Column(String(100), index=True, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
