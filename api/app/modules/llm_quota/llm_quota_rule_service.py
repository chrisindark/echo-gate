"""CRUD persistence for ``llm_quota_rules``.

Separated from :class:`~app.modules.llm_quota.llm_quota_service.LlmQuotaService`
on purpose: that service is a singleton bound to Redis and sits on the hot chat
path (``check_quota``/``record_tokens``), while this one only does database work
for the admin CRUD surface. ``LlmQuotaService.get_applicable_limits`` continues
to read the same table through its own session and is unaffected by this class.

Every method runs inside :func:`app.core.rls.system_rls_context`, which is
required: ``llm_quota_rules`` has Row-Level Security enabled and
``TenantContextMiddleware`` only ever binds a *caller* tenant, which would
otherwise limit this service to global rules plus the caller's own tenant.

Entering that context bypasses RLS for the whole transaction, so it must never be
reachable without authentication. Every public method therefore takes an
``admin: AdminKeyIdentity`` argument supplied by ``AdminApiKeyGuard``, and the
session refuses to open without one. That turns "someone forgot the guard on a new
admin route" from a silent RLS bypass into a loud error. It is fail-loud defence
in depth, **not** a security boundary — in-process Python can always call this
service directly. The real boundary is the guarded HTTP layer in
``llm_quota_controller.py``.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import SessionFactory, SessionLocal
from app.core.rls import system_rls_context
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.llm_quota.llm_quota_schema import (
    LlmQuotaRuleCreate,
    LlmQuotaRuleUpdate,
)

logger = logging.getLogger(__name__)


class UnauthenticatedAdminAccessError(RuntimeError):
    """Raised when cross-tenant quota access is attempted without an admin key."""


class LlmQuotaRuleService:
    """Admin CRUD for quota rules across every tenant."""

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

    @contextmanager
    def _get_session(self, admin: AdminKeyIdentity) -> Generator[Session, None, None]:
        # Guard rail, not a security boundary: entering the system RLS context
        # disables Row-Level Security for the transaction, so an unauthenticated
        # caller must never get one.
        #
        # The isinstance check (rather than `is None`) matters: a mis-wired
        # dependency that resolves to *something else* — for example passing the
        # guard class to Depends(), which FastAPI treats as a factory and
        # resolves to an un-invoked instance — would otherwise sail past a None
        # check and open a bypassed session.
        if not isinstance(admin, AdminKeyIdentity):
            raise UnauthenticatedAdminAccessError(
                "LlmQuotaRuleService opens sessions in system_rls_context(), which "
                "bypasses Row-Level Security. A resolved AdminKeyIdentity from "
                f"AdminApiKeyGuard is required; got {type(admin).__name__}."
            )
        # The system RLS context is entered before any transaction begins, so
        # the ``after_begin`` hook in app.core.rls binds the system GUC first.
        with system_rls_context():
            if self._db_session is not None:
                yield self._db_session
            elif self._session_factory is not None:
                with self._session_factory() as session:
                    yield session
            else:
                with SessionLocal() as session:
                    yield session

    def create_rule(
        self, data: LlmQuotaRuleCreate, admin: AdminKeyIdentity
    ) -> LlmQuotaRule:
        """Create a quota rule.

        ``tenant_id`` stays ``None`` for a global rule rather than being stamped
        with the caller, because ``_stamp_rls_identity`` skips system mode.
        """
        record = LlmQuotaRule(
            provider=data.provider,
            model=data.model,
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            max_rpm=data.max_rpm,
            max_tpm=data.max_tpm,
            is_active=data.is_active,
        )
        with self._get_session(admin) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def list_rules(
        self,
        admin: AdminKeyIdentity,
        skip: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[LlmQuotaRule], int]:
        """Return ``(rules, total_count)`` for the admin quota table view."""
        with self._get_session(admin) as session:
            filters = []
            if tenant_id is not None:
                filters.append(LlmQuotaRule.tenant_id == tenant_id)
            if provider is not None:
                filters.append(LlmQuotaRule.provider == provider)
            if model is not None:
                filters.append(LlmQuotaRule.model == model)
            if is_active is not None:
                filters.append(LlmQuotaRule.is_active.is_(is_active))

            total = session.execute(
                select(func.count()).select_from(LlmQuotaRule).where(*filters)
            ).scalar_one()

            rules = list(
                session.execute(
                    select(LlmQuotaRule)
                    .where(*filters)
                    .order_by(LlmQuotaRule.id)
                    .offset(skip)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return rules, total

    def get_rule(self, rule_id: int, admin: AdminKeyIdentity) -> LlmQuotaRule | None:
        with self._get_session(admin) as session:
            return session.get(LlmQuotaRule, rule_id)

    def update_rule(
        self, rule_id: int, data: LlmQuotaRuleUpdate, admin: AdminKeyIdentity
    ) -> LlmQuotaRule | None:
        """Apply a partial update. Returns ``None`` when the rule does not exist."""
        with self._get_session(admin) as session:
            record = session.get(LlmQuotaRule, rule_id)
            if record is None:
                return None

            updates = data.model_dump(exclude_unset=True)
            for field, value in updates.items():
                setattr(record, field, value)

            session.commit()
            session.refresh(record)
        return record

    def delete_rule(self, rule_id: int, admin: AdminKeyIdentity) -> bool:
        with self._get_session(admin) as session:
            record = session.get(LlmQuotaRule, rule_id)
            if record is None:
                return False
            session.delete(record)
            session.commit()
        return True

    def list_tenants(self, admin: AdminKeyIdentity) -> list[str]:
        """Distinct tenant IDs that have at least one quota rule.

        Drives the tenant filter in the admin UI. Global rules carry a NULL
        tenant and are reported separately by ``list_rules`` rather than here.
        """
        with self._get_session(admin) as session:
            rows = session.execute(
                select(LlmQuotaRule.tenant_id)
                .where(LlmQuotaRule.tenant_id.is_not(None))
                .distinct()
                .order_by(LlmQuotaRule.tenant_id)
            ).scalars()
            return list(rows)

    def preview_effective_limits(
        self,
        admin: AdminKeyIdentity,
        tenant_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        user: str | None = None,
    ) -> tuple[int | None, int | None]:
        """Most restrictive ``(max_rpm, max_tpm)`` that would apply to a scope.

        Mirrors the matching rules in ``LlmQuotaService.get_applicable_limits``
        (wildcards are ``IS NULL``) without touching Redis, so the UI can show
        an operator what a scope actually resolves to today.
        """
        with self._get_session(admin) as session:
            query = session.query(LlmQuotaRule).filter(LlmQuotaRule.is_active.is_(True))
            if provider:
                query = query.filter(
                    or_(
                        LlmQuotaRule.provider.is_(None),
                        LlmQuotaRule.provider == provider,
                    )
                )
            if model:
                query = query.filter(
                    or_(LlmQuotaRule.model.is_(None), LlmQuotaRule.model == model)
                )
            if user:
                query = query.filter(
                    or_(LlmQuotaRule.user_id.is_(None), LlmQuotaRule.user_id == user)
                )
            if tenant_id:
                query = query.filter(
                    or_(
                        LlmQuotaRule.tenant_id.is_(None),
                        LlmQuotaRule.tenant_id == tenant_id,
                    )
                )

            max_rpm: int | None = None
            max_tpm: int | None = None
            for rule in query.all():
                if rule.max_rpm is not None and (
                    max_rpm is None or rule.max_rpm < max_rpm
                ):
                    max_rpm = int(rule.max_rpm)
                if rule.max_tpm is not None and (
                    max_tpm is None or rule.max_tpm < max_tpm
                ):
                    max_tpm = int(rule.max_tpm)
            return max_rpm, max_tpm
