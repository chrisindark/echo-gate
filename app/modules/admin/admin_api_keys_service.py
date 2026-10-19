"""Creation, resolution, and revocation of admin API keys.

Keys can only be minted by the offline ``app.scripts.create_admin_key`` CLI.
There is deliberately no HTTP path that creates an admin key: admin keys mint
*privilege* (cross-tenant Postgres access via ``system_rls_context``), so
allowing one to be created over HTTP would mean the admin surface could grant
itself more admin. See ``docs/API_REFERENCE.md``.

Resolution differences from the tenant ``ApiKeyService``, all intentional:

* **Shorter cache.** Tenant keys cache for 30s; a revoked admin key should stop
  working almost immediately, so the cache is 5s and revocation clears it.
* **Negative results are not cached for long**, so a freshly minted key works on
  the next request rather than after a TTL.
* **Expiry is enforced on every resolve**, independent of the cache entry.
"""

import logging
import secrets
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionFactory, SessionLocal
from app.modules.admin.admin_api_keys_model import AdminApiKey
from app.modules.api_keys.api_keys_service import hash_api_key

logger = logging.getLogger(__name__)

# Distinct from the tenant key prefix so an admin key can never be mistaken for
# (or accidentally accepted as) a tenant key.
ADMIN_KEY_PREFIX = "ega_"

# Short enough that revocation is effectively prompt, long enough that a burst of
# admin UI requests does not each hit the database.
_RESOLVE_CACHE_TTL_SECONDS = 5.0
# Negative caching is deliberately shorter: a key minted a second ago must work
# immediately, not after the TTL.
_NEGATIVE_CACHE_TTL_SECONDS = 1.0

# Grant-all scope, equivalent to every specific scope.
ADMIN_WILDCARD_SCOPE = "admin:*"

# Known scopes. Validated on creation so a typo like "quota:reed" fails loudly at
# mint time instead of silently granting nothing.
KNOWN_SCOPES: frozenset[str] = frozenset(
    {
        "quota:read",
        "quota:write",
        "keys:read",
        "keys:write",
    }
)

# Writing last_used_at on every request would turn the admin guard into a write
# amplifier. Only record it once the recorded value is this stale.
_LAST_USED_WRITE_INTERVAL_SECONDS = 300.0


class InvalidScopeError(ValueError):
    """Raised when a requested scope is not a known scope."""


def normalize_scopes(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Validate and de-duplicate a scope list, preserving order.

    Accepts the comma-separated form stored in the column or an iterable.
    ``admin:*`` is a wildcard and stands alone.
    """
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [str(p).strip() for p in raw]

    parts = [p for p in parts if p]
    if not parts:
        raise InvalidScopeError("At least one scope is required.")

    unknown = [p for p in parts if p != ADMIN_WILDCARD_SCOPE and p not in KNOWN_SCOPES]
    if unknown:
        raise InvalidScopeError(
            f"Unknown scope(s): {', '.join(sorted(unknown))}. "
            f"Valid scopes: {', '.join(sorted(KNOWN_SCOPES))}, or {ADMIN_WILDCARD_SCOPE}."
        )

    if ADMIN_WILDCARD_SCOPE in parts:
        return [ADMIN_WILDCARD_SCOPE]

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def scopes_to_text(scopes: list[str]) -> str:
    return ",".join(scopes)


def scopes_from_text(text: str) -> set[str]:
    """Parse a stored scope string. Unknown values are ignored defensively."""
    return {p.strip() for p in (text or "").split(",") if p.strip()}


@dataclass(frozen=True)
class AdminKeyIdentity:
    """An authenticated admin key and the scopes it carries."""

    key_id: int
    name: str
    scopes: frozenset[str]
    # Carried so expiry can be re-checked on cache hits without a DB read.
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (now or datetime.now(UTC))

    def has_scope(self, required: str) -> bool:
        """True when this key carries ``required`` (wildcard grants everything).

        A key with ``quota:write`` also satisfies ``quota:read``: write implies
        read for this API, since every write endpoint can already read what it
        writes.
        """
        if ADMIN_WILDCARD_SCOPE in self.scopes:
            return True
        if required in self.scopes:
            return True
        if required.endswith(":read"):
            implied = required.removesuffix(":read") + ":write"
            if implied in self.scopes:
                return True
        return False


class AdminApiKeyService:
    def __init__(
        self,
        session_factory: SessionFactory | Session | None = None,
        db_session: Session | None = None,
    ) -> None:
        if isinstance(session_factory, Session):
            db_session = session_factory
            session_factory = None

        if session_factory is None and db_session is None:
            session_factory = SessionLocal

        self._session_factory: SessionFactory | None = session_factory
        self._db_session: Session | None = db_session
        # hash -> (expiry_monotonic, identity | None)
        self._resolve_cache: dict[str, tuple[float, AdminKeyIdentity | None]] = {}
        self._cache_lock = threading.Lock()

    @contextmanager
    def _get_session(self) -> Generator[Session, None, None]:
        if self._db_session is not None:
            yield self._db_session
        elif self._session_factory is not None:
            with self._session_factory() as session:
                yield session
        else:
            with SessionLocal() as session:
                yield session

    @staticmethod
    def generate_key() -> str:
        return f"{ADMIN_KEY_PREFIX}{secrets.token_urlsafe(32)}"

    def create_admin_key(
        self,
        name: str,
        scopes: str | list[str],
        expires_in_days: int | None = None,
    ) -> tuple[AdminApiKey, str]:
        """Create an admin key and return ``(record, plaintext_key)``.

        The plaintext is returned exactly once, at creation time. Only callers
        outside the HTTP layer should invoke this.
        """
        normalized = normalize_scopes(scopes)
        plaintext = self.generate_key()
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days
            else None
        )
        record = AdminApiKey(
            name=name,
            key_prefix=plaintext[: len(ADMIN_KEY_PREFIX) + 8],
            key_hash=hash_api_key(plaintext),
            scopes=scopes_to_text(normalized),
            expires_at=expires_at,
            is_active=True,
        )
        with self._get_session() as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record, plaintext

    def list_admin_keys(self) -> list[AdminApiKey]:
        """List keys, metadata only. Plaintext is never recoverable."""
        with self._get_session() as session:
            return list(
                session.execute(select(AdminApiKey).order_by(AdminApiKey.id))
                .scalars()
                .all()
            )

    def revoke_admin_key(self, key_id: int) -> bool:
        with self._get_session() as session:
            record = session.get(AdminApiKey, key_id)
            if record is None:
                return False
            record.is_active = False
            session.commit()
        self._clear_cache()
        return True

    def resolve_admin_key(self, plaintext: str) -> AdminKeyIdentity | None:
        """Resolve a plaintext admin key to its identity and scopes.

        Returns ``None`` for empty, unknown, inactive, or expired keys.
        """
        if not plaintext:
            return None

        cache_key = hash_api_key(plaintext)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._resolve_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                identity = cached[1]
                # Re-check expiry on cache hits too. The cached entry is only
                # valid up to the key's own expiry, so a key cannot keep
                # authenticating for the remainder of the cache TTL after it
                # lapses. This is why the cache entry also carries the deadline.
                if identity is None or not self._is_expired(identity):
                    return identity

        identity = None
        try:
            with self._get_session() as session:
                record = session.execute(
                    select(AdminApiKey).where(
                        AdminApiKey.key_hash == cache_key,
                        AdminApiKey.is_active.is_(True),
                    )
                ).scalar_one_or_none()

                if record is not None and not self._is_expired_record(record):
                    identity = AdminKeyIdentity(
                        key_id=record.id,
                        name=record.name,
                        scopes=frozenset(scopes_from_text(record.scopes)),
                        expires_at=self._as_utc(record.expires_at),
                    )
                    self._touch_last_used(session, record)
        except Exception:
            # Never let a lookup failure authenticate anyone, and never let it
            # 500 the guard: an unresolvable key is simply not valid.
            logger.exception("Failed to resolve admin API key")
            return None

        ttl = (
            _RESOLVE_CACHE_TTL_SECONDS
            if identity is not None
            else _NEGATIVE_CACHE_TTL_SECONDS
        )
        if identity is not None and identity.expires_at is not None:
            # Never cache a valid result past the key's own expiry.
            remaining = (identity.expires_at - datetime.now(UTC)).total_seconds()
            ttl = min(ttl, max(remaining, 0.0))
        with self._cache_lock:
            self._resolve_cache[cache_key] = (now + ttl, identity)
        return identity

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        """Normalize a possibly-naive datetime from the DB to UTC."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @classmethod
    def _is_expired(cls, identity: AdminKeyIdentity) -> bool:
        return identity.is_expired()

    @classmethod
    def _is_expired_record(cls, record: AdminApiKey) -> bool:
        expires_at = cls._as_utc(record.expires_at)
        return expires_at is not None and expires_at <= datetime.now(UTC)

    @staticmethod
    def _touch_last_used(session: Session, record: AdminApiKey) -> None:
        """Record key usage, throttled to avoid a write per request."""
        now = datetime.now(UTC)
        last_used = record.last_used_at
        if last_used is not None and last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=UTC)
        if last_used is not None and (now - last_used).total_seconds() < (
            _LAST_USED_WRITE_INTERVAL_SECONDS
        ):
            return
        try:
            record.last_used_at = now
            session.commit()
        except Exception:
            # Usage telemetry must never fail the request being authenticated.
            logger.warning("Failed to record admin key last_used_at", exc_info=True)
            session.rollback()

    def _clear_cache(self) -> None:
        with self._cache_lock:
            self._resolve_cache.clear()


# Shared instance used by the admin guard.
admin_api_key_service = AdminApiKeyService()
