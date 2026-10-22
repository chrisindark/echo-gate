"""Admin API key storage.

Admin keys authenticate the cross-tenant admin surface (``/api/v1/admin/*``).
They are stored separately from tenant ``api_keys`` on purpose — see the
migration docstring and ``docs/API_REFERENCE.md`` for the reasoning.

Like ``api_keys``, this table is intentionally *not* under RLS: admin key
resolution happens before any tenant context exists, so the row must be readable
regardless of the active tenant. Lookups are always by exact ``key_hash``.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.core.database import Base


class AdminApiKey(Base):
    """An operator credential granting scoped access to the admin API."""

    __tablename__ = "admin_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    # Human label for the holder ("ops", "ci-deploy"). Not unique: one operator
    # may legitimately hold several keys (rotation, per-environment).
    name = Column(String(100), nullable=False)
    # Leading characters of the plaintext, for display in `list` only.
    key_prefix = Column(String(16), index=True, nullable=False)
    # SHA-256 hex digest. The plaintext is shown once at creation and is never
    # recoverable from this table.
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    # Comma-separated scopes, e.g. "quota:read,quota:write". "admin:*" grants all.
    scopes = Column(String(255), nullable=False)
    # NULL means the key does not expire on its own (revocation is explicit).
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    # Throttled by the service rather than written on every request.
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
