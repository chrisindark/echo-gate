"""Postgres Row-Level Security (RLS) context management.

The gateway connects to Postgres as a non-owner role (``DB_APP_ROLE``), so the
RLS policies defined in the Alembic baseline apply to every statement it runs.
The active tenant/user for a transaction is delivered to Postgres through three
custom GUCs:

* ``app.current_tenant`` -- the tenant the caller is acting as.
* ``app.current_user``   -- the caller identity within that tenant (optional).
* ``app.rls_system``     -- ``'on'`` for trusted background jobs that must
  operate across every tenant.

Values are set as *transaction-local* settings (``set_config(..., is_local =>
true)``) on every transaction begin, so a pooled connection can never leak one
request's tenant context into the next one. When no context is active the GUCs
are absent, every policy comparison evaluates to ``NULL`` and no rows are
visible -- a fail-closed default.
"""

import contextlib
import logging
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ANONYMOUS_TENANT = "anonymous"

# Columns stamped from the active RLS context when the caller leaves them unset.
_TENANT_COLUMN = "tenant_id"
_USER_COLUMN = "user_id"


@dataclass(frozen=True)
class RlsContext:
    tenant_id: str = ANONYMOUS_TENANT
    user_id: str | None = None
    system: bool = False


_current_rls_context: ContextVar[RlsContext | None] = ContextVar(
    "current_rls_context", default=None
)

_MISSING = object()


def get_rls_context() -> RlsContext:
    """Return the active context, defaulting to the anonymous tenant."""
    return _current_rls_context.get() or RlsContext()


@contextlib.contextmanager
def rls_context(
    tenant_id: str | None,
    user_id: str | None = None,
    system: bool = False,
) -> Generator[RlsContext]:
    """Bind the RLS context (tenant/user/system) for the duration of the block."""
    ctx = RlsContext(
        tenant_id=tenant_id or ANONYMOUS_TENANT,
        user_id=user_id or None,
        system=system,
    )
    token = _current_rls_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_rls_context.reset(token)


@contextlib.contextmanager
def system_rls_context() -> Generator[RlsContext]:
    """Cross-tenant system context for trusted background jobs."""
    with rls_context(tenant_id=ANONYMOUS_TENANT, system=True) as ctx:
        yield ctx


@event.listens_for(Session, "after_begin")
def _apply_rls_context(session, transaction, connection) -> None:
    """Bind tenant/user GUCs to the transaction that just began."""
    if connection.dialect.name != "postgresql":
        return
    ctx = get_rls_context()
    try:
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": ctx.tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.current_user', :user, true)"),
            {"user": ctx.user_id or ""},
        )
        connection.execute(
            text("SELECT set_config('app.rls_system', :system, true)"),
            {"system": "on" if ctx.system else "off"},
        )
    except Exception:  # pragma: no cover - defensive, never break a request
        logger.exception("Failed to apply RLS context to transaction")


@event.listens_for(Session, "before_flush")
def _stamp_rls_identity(flush_context, session, instances) -> None:
    """Stamp tenant/user identity on new rows when callers leave them unset.

    RLS ``WITH CHECK`` clauses reject rows that do not match the active tenant,
    so a caller that never populated ``tenant_id`` would otherwise fail the
    insert. System-mode writes are left untouched (they explicitly own their
    rows, e.g. global quota rules with a NULL tenant).
    """
    ctx = get_rls_context()
    if ctx.system:
        return
    for obj in flush_context.new:
        if getattr(obj, _TENANT_COLUMN, _MISSING) is _MISSING:
            continue
        if getattr(obj, _TENANT_COLUMN) is None:
            obj.tenant_id = ctx.tenant_id
        if getattr(obj, _USER_COLUMN, _MISSING) is not _MISSING:
            if getattr(obj, _USER_COLUMN) is None and ctx.user_id:
                obj.user_id = ctx.user_id
