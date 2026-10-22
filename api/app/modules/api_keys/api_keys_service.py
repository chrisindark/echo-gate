import hashlib
import logging
import secrets
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionFactory, SessionLocal
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.api_keys.api_keys_schema import ApiKeyCreate

logger = logging.getLogger(__name__)

_KEY_PREFIX = "eg_"
_RESOLVE_CACHE_TTL_SECONDS = 30.0


def hash_api_key(api_key: str) -> str:
    """Return the stable SHA-256 hex digest used to store/lookup API keys."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiKeyIdentity:
    """Tenant/user identity resolved from an API key."""

    api_key_id: int
    tenant_id: str
    user_id: str | None


class ApiKeyService:
    def __init__(self, session_factory: SessionFactory | Session | None = None) -> None:
        if isinstance(session_factory, Session):
            self._db_session: Session | None = session_factory
            self._session_factory: SessionFactory = SessionLocal
        else:
            self._db_session = None
            self._session_factory = session_factory or SessionLocal
        # hash -> (expiry_monotonic, identity | None)
        self._resolve_cache: dict[str, tuple[float, ApiKeyIdentity | None]] = {}

    @contextmanager
    def _get_session(self) -> Generator[Session]:
        if self._db_session is not None:
            yield self._db_session
        else:
            with self._session_factory() as session:
                yield session

    @staticmethod
    def generate_key() -> str:
        return f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"

    def create_api_key(self, data: ApiKeyCreate) -> tuple[ApiKey, str]:
        """Create a key and return ``(record, plaintext_key)``.

        The plaintext key is only ever returned once, at creation time.
        """
        plaintext = self.generate_key()
        record = ApiKey(
            name=data.name,
            key_prefix=plaintext[: len(_KEY_PREFIX) + 8],
            key_hash=hash_api_key(plaintext),
            tenant_id=data.tenant_id,
            user_id=data.user_id,
            is_active=True,
        )
        with self._get_session() as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record, plaintext

    def list_api_keys(self, skip: int = 0, limit: int = 100) -> list[ApiKey]:
        with self._get_session() as session:
            stmt = select(ApiKey).order_by(ApiKey.id).offset(skip).limit(limit)
            return list(session.execute(stmt).scalars().all())

    def revoke_api_key(self, key_id: int) -> bool:
        with self._get_session() as session:
            record = session.get(ApiKey, key_id)
            if record is None:
                return False
            record.is_active = False
            session.commit()
        self._resolve_cache.clear()
        return True

    def rotate_api_key(
        self, key_id: int, grace_hours: int = 24
    ) -> tuple[ApiKey, str, ApiKey] | None:
        """Rotate an API key: generate a new active key and set old key's expiry."""
        with self._get_session() as session:
            old_record = session.get(ApiKey, key_id)
            if old_record is None or not old_record.is_active:
                return None

            plaintext = self.generate_key()
            new_record = ApiKey(
                name=f"{old_record.name} (rotated)",
                key_prefix=plaintext[: len(_KEY_PREFIX) + 8],
                key_hash=hash_api_key(plaintext),
                tenant_id=old_record.tenant_id,
                user_id=old_record.user_id,
                is_active=True,
                expires_at=None,
            )
            session.add(new_record)
            old_record.expires_at = datetime.now(UTC) + timedelta(hours=grace_hours)
            session.commit()
            session.refresh(new_record)
            session.refresh(old_record)

        self._resolve_cache.clear()
        return new_record, plaintext, old_record

    def resolve_api_key(self, api_key: str) -> ApiKeyIdentity | None:
        """Resolve a plaintext API key to its tenant/user identity.

        Results are cached briefly so the per-request lookup does not hit the
        database on every call. A miss, an inactive key, or an expired key returns ``None``.
        """
        if not api_key:
            return None

        cache_key = hash_api_key(api_key)
        cached = self._resolve_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        identity: ApiKeyIdentity | None = None
        try:
            with self._get_session() as session:
                record = session.execute(
                    select(ApiKey).where(
                        ApiKey.key_hash == cache_key,
                        ApiKey.is_active.is_(True),
                    )
                ).scalar_one_or_none()
                if record is not None:
                    if (
                        record.expires_at is not None
                        and record.expires_at < datetime.now(UTC)
                    ):
                        logger.info(
                            "API key %d has expired (expires_at=%s)",
                            record.id,
                            record.expires_at,
                        )
                        return None
                    identity = ApiKeyIdentity(
                        api_key_id=record.id,
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                    )
        except Exception:
            logger.exception("Failed to resolve API key")
            return None

        self._resolve_cache[cache_key] = (now + _RESOLVE_CACHE_TTL_SECONDS, identity)
        return identity


def get_api_key_service() -> ApiKeyService:
    """FastAPI dependency returning the shared API key service."""
    return api_key_service


# Shared instance used by both the controllers and the tenant-context middleware.
api_key_service = ApiKeyService()
