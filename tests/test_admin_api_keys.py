"""Admin API key and guard tests.

Covers the security properties the admin surface depends on:

* A tenant API key is never accepted as an admin key (separate tables, separate
  prefixes).
* The guard fails closed: missing, unknown, inactive, and expired keys are all
  rejected.
* Scopes are enforced, and a ``:write`` scope implies the matching ``:read``.
* ``require_admin_scope`` returns an *instance*, not the guard class. Passing a
  class to ``Depends`` makes FastAPI treat it as a dependency factory and resolve
  to an un-invoked instance, which silently disables the guard entirely; that
  regression is pinned below.

The database-backed tests skip when Postgres is unreachable:

    DATABASE_URL=postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate \
        poetry run pytest tests/test_admin_api_keys.py -v
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, text

from app.core.config import config
from app.core.database import SessionLocal
from app.core.guards import AdminApiKeyGuard, require_admin_scope
from app.core.rls import system_rls_context
from app.modules.admin.admin_api_keys_model import AdminApiKey
from app.modules.admin.admin_api_keys_service import (
    _RESOLVE_CACHE_TTL_SECONDS,
    ADMIN_KEY_PREFIX,
    ADMIN_WILDCARD_SCOPE,
    AdminApiKeyService,
    AdminKeyIdentity,
    InvalidScopeError,
    normalize_scopes,
    scopes_from_text,
    scopes_to_text,
)
from app.modules.api_keys.api_keys_service import ApiKeyService


def _database_ready() -> bool:
    try:
        probe = create_engine(config.DATABASE_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as connection:
            connection.execute(text("SELECT 1 FROM admin_api_keys LIMIT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_ready(),
    reason="Postgres with the admin_api_keys migration is not reachable",
)

TEST_PREFIX = "itest_admin"


def _cleanup() -> None:
    with system_rls_context():
        with SessionLocal() as session:
            session.execute(
                delete(AdminApiKey).where(AdminApiKey.name.like(f"{TEST_PREFIX}%"))
            )
            session.commit()


def _request():
    return MagicMock(spec=["state", "headers"])


# --- Scope parsing (no database) ---------------------------------------------


def test_normalize_scopes_trims_and_dedupes():
    assert normalize_scopes("quota:read, quota:write") == ["quota:read", "quota:write"]
    assert normalize_scopes(["quota:read", "quota:read"]) == ["quota:read"]
    assert normalize_scopes(["keys:write", " keys:write "]) == ["keys:write"]


def test_normalize_scopes_rejects_unknown_and_empty():
    with pytest.raises(InvalidScopeError):
        normalize_scopes("quota:reed")
    with pytest.raises(InvalidScopeError):
        normalize_scopes("")
    with pytest.raises(InvalidScopeError):
        normalize_scopes([])


def test_wildcard_stands_alone():
    assert normalize_scopes([ADMIN_WILDCARD_SCOPE, "quota:read"]) == [
        ADMIN_WILDCARD_SCOPE
    ]


def test_scope_round_trip_through_text():
    scopes = ["quota:read", "keys:write"]
    assert scopes_from_text(scopes_to_text(scopes)) == set(scopes)


def test_wildcard_grants_everything():
    identity = AdminKeyIdentity(
        key_id=1, name="root", scopes=frozenset({ADMIN_WILDCARD_SCOPE})
    )
    assert identity.has_scope("quota:read")
    assert identity.has_scope("quota:write")
    assert identity.has_scope("keys:write")


def test_write_scope_implies_read():
    identity = AdminKeyIdentity(key_id=1, name="rw", scopes=frozenset({"quota:write"}))
    assert identity.has_scope("quota:read")
    assert identity.has_scope("quota:write")
    # ...but not across resources.
    assert not identity.has_scope("keys:write")


def test_read_scope_does_not_imply_write():
    identity = AdminKeyIdentity(key_id=1, name="ro", scopes=frozenset({"quota:read"}))
    assert identity.has_scope("quota:read")
    assert not identity.has_scope("quota:write")


def test_unrelated_scope_is_denied():
    identity = AdminKeyIdentity(
        key_id=1, name="keys-only", scopes=frozenset({"keys:read"})
    )
    assert not identity.has_scope("quota:read")


# --- Guard wiring (no database) ----------------------------------------------


def test_require_admin_scope_returns_an_instance_not_a_class():
    """Regression guard.

    FastAPI treats a *class* passed to Depends() as a dependency factory: it
    instantiates the class and uses that instance as the resolved value without
    ever calling __call__. If require_admin_scope returned the class, every
    admin route would be wide open while still appearing to be guarded.
    """
    guard = require_admin_scope("quota:read")
    assert not isinstance(guard, type), "must return an instance, not a class"
    assert isinstance(guard, AdminApiKeyGuard)
    assert callable(guard.__call__)


def test_require_admin_scope_returns_distinct_instances():
    assert require_admin_scope("quota:read") is not require_admin_scope("quota:read")


def test_guard_rejects_missing_key():
    guard = AdminApiKeyGuard()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(guard(request=_request(), x_admin_key=None))
    assert exc.value.status_code == 401
    assert "Admin API Key missing" in exc.value.detail


def test_guard_rejects_invalid_key():
    guard = AdminApiKeyGuard()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(guard(request=_request(), x_admin_key="ega_not_a_real_key"))
    assert exc.value.status_code == 401
    # The message must not distinguish "unknown" from "revoked" so the endpoint
    # cannot be used to enumerate which keys exist.
    assert "Invalid, inactive, or expired" in exc.value.detail


def test_scoped_guard_denies_insufficient_scope():
    identity = AdminKeyIdentity(key_id=1, name="ro", scopes=frozenset({"quota:read"}))
    guard = require_admin_scope("quota:write")
    request = _request()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_invoke_with_identity(guard, request, "ega_key", identity))
    assert exc.value.status_code == 403
    assert "quota:write" in exc.value.detail


async def _invoke_with_identity(guard, request, key, identity):
    """Call a guard with the service resolution stubbed to return ``identity``."""
    import app.modules.admin.admin_api_keys_service as svc

    original = svc.admin_api_key_service.resolve_admin_key
    svc.admin_api_key_service.resolve_admin_key = lambda _k: identity
    try:
        return await guard(request=request, x_admin_key=key)
    finally:
        svc.admin_api_key_service.resolve_admin_key = original


def test_scoped_guard_allows_sufficient_scope_and_stamps_state():
    identity = AdminKeyIdentity(key_id=7, name="ops", scopes=frozenset({"quota:read"}))
    guard = require_admin_scope("quota:read")
    request = _request()
    result = asyncio.run(_invoke_with_identity(guard, request, "ega_key", identity))
    assert result == identity
    assert request.state.admin_key_id == 7
    assert request.state.admin_key_name == "ops"


# --- Service (real Postgres) -------------------------------------------------


@requires_db
def test_generated_key_uses_the_admin_prefix():
    key = AdminApiKeyService.generate_key()
    assert key.startswith(ADMIN_KEY_PREFIX)
    assert not key.startswith("eg_")
    assert len(key) > len(ADMIN_KEY_PREFIX) + 20


def test_tenant_keys_and_admin_keys_use_different_prefixes():
    """A tenant key must never be mistakable for an admin key."""
    assert not ApiKeyService.generate_key().startswith(ADMIN_KEY_PREFIX)
    assert not AdminApiKeyService.generate_key().startswith("eg_")


@requires_db
def test_create_resolve_revoke_round_trip():
    _cleanup()
    service = AdminApiKeyService()

    try:
        record, plaintext = service.create_admin_key(
            f"{TEST_PREFIX}-ops", "quota:read,quota:write", expires_in_days=30
        )
        assert plaintext.startswith(ADMIN_KEY_PREFIX)
        assert record.key_prefix == plaintext[: len(ADMIN_KEY_PREFIX) + 8]
        # The plaintext is never stored.
        assert plaintext not in record.key_hash
        assert len(record.key_hash) == 64

        identity = service.resolve_admin_key(plaintext)
        assert identity is not None
        assert identity.key_id == record.id
        assert identity.has_scope("quota:write")
        assert not identity.has_scope("keys:write")

        assert service.revoke_admin_key(record.id) is True
        # Revocation clears the local cache, so the key stops resolving at once.
        assert service.resolve_admin_key(plaintext) is None
    finally:
        _cleanup()


@requires_db
def test_resolve_rejects_unknown_and_empty_keys():
    _cleanup()
    service = AdminApiKeyService()
    assert service.resolve_admin_key("") is None
    assert service.resolve_admin_key(None) is None
    assert service.resolve_admin_key("ega_definitely_not_a_key") is None


@requires_db
def test_already_expired_key_does_not_resolve():
    """A key whose expiry has passed must never authenticate."""
    _cleanup()
    service = AdminApiKeyService()

    try:
        record, plaintext = service.create_admin_key(f"{TEST_PREFIX}-exp", "quota:read")

        # Backdate the stored expiry so the row is already past it.
        with system_rls_context():
            with SessionLocal() as session:
                row = session.get(AdminApiKey, record.id)
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()

        assert service.resolve_admin_key(plaintext) is None
    finally:
        _cleanup()


@requires_db
def test_expiry_is_rechecked_on_cache_hits():
    """A key must stop working the moment it lapses, not at the cache TTL.

    Without a re-check on the cache-hit path, a key whose ``expires_at`` passes
    would keep authenticating until its cache entry aged out. This injects a
    cache entry whose deadline has already passed and asserts it is not served.
    """
    _cleanup()
    service = AdminApiKeyService()

    try:
        _, plaintext = service.create_admin_key(f"{TEST_PREFIX}-soon", "quota:read")

        lapsed = AdminKeyIdentity(
            key_id=999,
            name="lapsed",
            scopes=frozenset({"quota:read"}),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        # A cache entry that is still "fresh" by TTL but past its own expiry.
        with service._cache_lock:
            service._resolve_cache[hashlib.sha256(plaintext.encode()).hexdigest()] = (
                time.monotonic() + _RESOLVE_CACHE_TTL_SECONDS,
                lapsed,
            )

        # The stale entry must not be served. The resolver then falls through to
        # a fresh database read, which finds the real key (created without an
        # expiry) and authenticates it -- so assert on the *served* identity, not
        # on a None result that only holds if the fallback also comes up empty.
        served = service.resolve_admin_key(plaintext)
        assert served is None or served.key_id != lapsed.key_id
    finally:
        _cleanup()


@requires_db
def test_a_cached_nonexpiring_key_is_served():
    """The other half of the re-check: a valid cached key still authenticates."""
    _cleanup()
    service = AdminApiKeyService()

    try:
        _, plaintext = service.create_admin_key(f"{TEST_PREFIX}-ok", "quota:read")
        assert service.resolve_admin_key(plaintext) is not None
        # Second call is served from the cache and must still be valid.
        assert service.resolve_admin_key(plaintext) is not None
    finally:
        _cleanup()


def test_cached_identity_expires_by_its_own_deadline():
    """Unit-level check of the cache-hit re-check, independent of the database."""
    past = AdminKeyIdentity(
        key_id=1, name="lapsed", scopes=frozenset({"quota:read"}), expires_at=None
    )
    assert not past.is_expired()

    lapsed = AdminKeyIdentity(
        key_id=2,
        name="lapsed",
        scopes=frozenset({"quota:read"}),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert lapsed.is_expired()

    future = AdminKeyIdentity(
        key_id=3,
        name="valid",
        scopes=frozenset({"quota:read"}),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert not future.is_expired()


@requires_db
def test_inactive_key_does_not_resolve():
    _cleanup()
    service = AdminApiKeyService()

    try:
        record, plaintext = service.create_admin_key(f"{TEST_PREFIX}-off", "quota:read")
        with system_rls_context():
            with SessionLocal() as session:
                session.get(AdminApiKey, record.id).is_active = False
                session.commit()
        service._clear_cache()
        assert service.resolve_admin_key(plaintext) is None
    finally:
        _cleanup()


@requires_db
def test_a_tenant_api_key_never_resolves_as_an_admin_key():
    """Separate stores: a tenant credential must not grant admin access."""
    _cleanup()
    admin_service = AdminApiKeyService()

    try:
        _, tenant_key = ApiKeyService().create_api_key(
            __import__(
                "app.modules.api_keys.api_keys_schema", fromlist=["ApiKeyCreate"]
            ).ApiKeyCreate(name=f"{TEST_PREFIX}-tenant", tenant_id=TEST_PREFIX)
        )
        assert tenant_key.startswith("eg_")
        assert admin_service.resolve_admin_key(tenant_key) is None
    finally:
        _cleanup()
