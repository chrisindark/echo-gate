"""Admin CRUD & hot-path policy resolution for ``tenant_privacy_policies``.

Admin mutations and cross-tenant reads run inside :func:`system_rls_context`
and require an authenticated :class:`AdminKeyIdentity` from
``AdminApiKeyGuard``. Runtime serving lookups read under the caller's tenant
context with a short-lived thread-safe in-memory TTL cache.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionFactory, SessionLocal
from app.core.rls import (
    ANONYMOUS_TENANT,
    get_rls_context,
    rls_context,
    system_rls_context,
)
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.guardrails.guardrails_schema import GuardrailsConfig
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.tenant_policy.tenant_policy_model import TenantPrivacyPolicy
from app.modules.tenant_policy.tenant_policy_schema import (
    EffectiveTenantPolicy,
    TenantPrivacyPolicyUpsert,
)

logger = logging.getLogger(__name__)

_POLICY_CACHE_TTL_SECONDS = 15.0
_NEGATIVE_CACHE_TTL_SECONDS = 5.0


class UnauthenticatedAdminPolicyAccessError(RuntimeError):
    """Raised when cross-tenant policy access is attempted without an admin key."""


def apply_tenant_policy_to_request(
    request: Any, policy: EffectiveTenantPolicy | None
) -> None:
    """Enforce a tenant's active privacy & cache restriction onto ``request``.

    Precedence works as an administrative compliance override:
    - ``zero_retention=True`` forces ``disable_log=True`` and ``cache_enabled=False``.
    - ``semantic_cache_enabled=False`` forces ``cache_enabled=False`` (even if
      the caller asked for ``cache_enabled: true``).
    - ``pii_scrubbing_mode == 'enforced'`` forces ``redact_pii=True``.
    - ``pii_scrubbing_mode == 'disabled'`` forces ``redact_pii=False`` for
      maximum accuracy & throughput.
    - ``guardrails_mode`` in ``('block', 'inject', 'off')`` applies org-level
      guardrail enforcement.
    """
    if policy is None or not policy.is_active:
        return

    if policy.zero_retention:
        request.disable_log = True
        request.cache_enabled = False
    elif not policy.semantic_cache_enabled:
        request.cache_enabled = False

    if policy.pii_scrubbing_mode == "enforced":
        request.redact_pii = True
    elif policy.pii_scrubbing_mode == "disabled":
        request.redact_pii = False

    if policy.guardrails_mode in ("block", "inject"):
        current_g = getattr(request, "guardrails", None)
        if isinstance(current_g, GuardrailsConfig):
            current_g.enabled = True
            if policy.guardrails_mode == "block" or current_g.mode != "block":
                current_g.mode = policy.guardrails_mode
        elif policy.guardrails_mode == "block" or current_g != "block":
            request.guardrails = policy.guardrails_mode
    elif policy.guardrails_mode == "off":
        request.guardrails = "off"


class TenantPolicyService:
    """Manages and resolves per-tenant privacy & caching restrictions."""

    def __init__(
        self,
        session_factory: SessionFactory | Session | None = None,
        session_factory_read: SessionFactory | Session | None = None,
        db_session: Session | None = None,
    ) -> None:
        if isinstance(session_factory, Session):
            db_session = session_factory
            session_factory = None
        if isinstance(session_factory_read, Session):
            db_session = session_factory_read
            session_factory_read = None

        if session_factory is None and db_session is None:
            session_factory = SessionLocal
        if session_factory_read is None and db_session is None:
            session_factory_read = session_factory

        self._session_factory: SessionFactory | None = session_factory
        self._session_factory_read: SessionFactory | None = session_factory_read
        self._db_session: Session | None = db_session

        # tenant_id -> (expires_monotonic, EffectiveTenantPolicy | None)
        self._cache: dict[str, tuple[float, EffectiveTenantPolicy | None]] = {}
        self._cache_lock = threading.Lock()

    @contextmanager
    def _get_admin_session(
        self, admin: AdminKeyIdentity
    ) -> Generator[Session, None, None]:
        if not isinstance(admin, AdminKeyIdentity):
            raise UnauthenticatedAdminPolicyAccessError(
                "TenantPolicyService admin operations enter system_rls_context(), "
                "which bypasses Row-Level Security. A resolved AdminKeyIdentity is "
                f"required; got {type(admin).__name__}."
            )
        with system_rls_context():
            if self._db_session is not None:
                yield self._db_session
            elif self._session_factory is not None:
                with self._session_factory() as session:
                    yield session
            else:
                with SessionLocal() as session:
                    yield session

    @contextmanager
    def _get_runtime_session(self, tenant_id: str) -> Generator[Session, None, None]:
        ctx = get_rls_context()
        rls_cm = (
            nullcontext()
            if (ctx.system or ctx.tenant_id == tenant_id)
            else rls_context(tenant_id=tenant_id)
        )
        with rls_cm:
            if self._db_session is not None:
                yield self._db_session
            elif self._session_factory_read is not None:
                with self._session_factory_read() as session:
                    yield session
            elif self._session_factory is not None:
                with self._session_factory() as session:
                    yield session
            else:
                with SessionLocal() as session:
                    yield session

    def invalidate_cache(self, tenant_id: str | None = None) -> None:
        with self._cache_lock:
            if tenant_id is None:
                self._cache.clear()
            else:
                self._cache.pop(tenant_id.strip() or ANONYMOUS_TENANT, None)

    @staticmethod
    def _to_snapshot(record: TenantPrivacyPolicy) -> EffectiveTenantPolicy:
        return EffectiveTenantPolicy(
            tenant_id=record.tenant_id,
            semantic_cache_enabled=bool(record.semantic_cache_enabled),
            zero_retention=bool(record.zero_retention),
            pii_scrubbing_mode=record.pii_scrubbing_mode,  # type: ignore[arg-type]
            guardrails_mode=record.guardrails_mode,  # type: ignore[arg-type]
            is_active=bool(record.is_active),
        )

    def get_active_policy(self, tenant_id: str | None) -> EffectiveTenantPolicy | None:
        """Resolve active privacy policy for ``tenant_id`` (cached)."""
        resolved_tenant = (tenant_id or "").strip() or ANONYMOUS_TENANT
        now = time.monotonic()

        stale_snapshot: EffectiveTenantPolicy | None = None
        with self._cache_lock:
            cached = self._cache.get(resolved_tenant)
            if cached is not None:
                expires_at, snap = cached
                if expires_at > now:
                    return snap
                stale_snapshot = snap

        snapshot: EffectiveTenantPolicy | None = None
        try:
            with self._get_runtime_session(resolved_tenant) as session:
                record = session.execute(
                    select(TenantPrivacyPolicy).where(
                        TenantPrivacyPolicy.tenant_id == resolved_tenant,
                        TenantPrivacyPolicy.is_active.is_(True),
                    )
                ).scalar_one_or_none()
                if record is not None:
                    snapshot = self._to_snapshot(record)
        except Exception:
            # Keep tests and transient offline environments non-fatal while
            # retaining the last known enforcement snapshot if one was cached.
            logger.debug(
                "Failed to resolve tenant privacy policy for %s",
                resolved_tenant,
                exc_info=True,
            )
            if stale_snapshot is not None:
                return stale_snapshot
            return None

        ttl = (
            _POLICY_CACHE_TTL_SECONDS
            if snapshot is not None
            else _NEGATIVE_CACHE_TTL_SECONDS
        )
        with self._cache_lock:
            self._cache[resolved_tenant] = (now + ttl, snapshot)
        return snapshot

    def resolve_and_apply(self, request: Any) -> EffectiveTenantPolicy | None:
        """Look up the caller's tenant policy and enforce it onto ``request``."""
        tenant_id = getattr(request, "tenant_id", None) or ANONYMOUS_TENANT
        policy = self.get_active_policy(tenant_id)
        apply_tenant_policy_to_request(request, policy)
        return policy

    # ── Admin CRUD operations ────────────────────────────────────────────────

    def list_policies(
        self,
        admin: AdminKeyIdentity,
        *,
        skip: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[TenantPrivacyPolicy], int]:
        with self._get_admin_session(admin) as session:
            filters = []
            if tenant_id is not None:
                filters.append(TenantPrivacyPolicy.tenant_id == tenant_id.strip())
            if is_active is not None:
                filters.append(TenantPrivacyPolicy.is_active.is_(is_active))

            total = session.execute(
                select(func.count()).select_from(TenantPrivacyPolicy).where(*filters)
            ).scalar_one()

            items = list(
                session.execute(
                    select(TenantPrivacyPolicy)
                    .where(*filters)
                    .order_by(TenantPrivacyPolicy.tenant_id)
                    .offset(skip)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return items, int(total)

    def get_policy(
        self, tenant_id: str, admin: AdminKeyIdentity
    ) -> TenantPrivacyPolicy | None:
        clean_tenant = tenant_id.strip()
        with self._get_admin_session(admin) as session:
            return session.execute(
                select(TenantPrivacyPolicy).where(
                    TenantPrivacyPolicy.tenant_id == clean_tenant
                )
            ).scalar_one_or_none()

    def upsert_policy(
        self,
        tenant_id: str,
        data: TenantPrivacyPolicyUpsert,
        admin: AdminKeyIdentity,
    ) -> TenantPrivacyPolicy:
        clean_tenant = tenant_id.strip()
        if not clean_tenant:
            raise ValueError("tenant_id must not be empty")

        with self._get_admin_session(admin) as session:
            record = session.execute(
                select(TenantPrivacyPolicy).where(
                    TenantPrivacyPolicy.tenant_id == clean_tenant
                )
            ).scalar_one_or_none()

            if record is None:
                record = TenantPrivacyPolicy(
                    tenant_id=clean_tenant,
                    semantic_cache_enabled=data.semantic_cache_enabled,
                    zero_retention=data.zero_retention,
                    pii_scrubbing_mode=data.pii_scrubbing_mode,
                    guardrails_mode=data.guardrails_mode,
                    description=data.description,
                    is_active=data.is_active,
                )
                session.add(record)
            else:
                record.semantic_cache_enabled = data.semantic_cache_enabled
                record.zero_retention = data.zero_retention
                record.pii_scrubbing_mode = data.pii_scrubbing_mode
                record.guardrails_mode = data.guardrails_mode
                record.description = data.description
                record.is_active = data.is_active

            session.commit()
            session.refresh(record)

        self.invalidate_cache(clean_tenant)
        return record

    def delete_policy(self, tenant_id: str, admin: AdminKeyIdentity) -> bool:
        clean_tenant = tenant_id.strip()
        with self._get_admin_session(admin) as session:
            record = session.execute(
                select(TenantPrivacyPolicy).where(
                    TenantPrivacyPolicy.tenant_id == clean_tenant
                )
            ).scalar_one_or_none()
            if record is None:
                return False
            session.delete(record)
            session.commit()

        self.invalidate_cache(clean_tenant)
        return True

    def list_tenants(self, admin: AdminKeyIdentity) -> list[str]:
        """Return distinct known tenant ids across policies, API keys, and quota rules."""
        with self._get_admin_session(admin) as session:
            policy_tenants = (
                session.execute(select(TenantPrivacyPolicy.tenant_id).distinct())
                .scalars()
                .all()
            )
            key_tenants = (
                session.execute(
                    select(ApiKey.tenant_id)
                    .where(ApiKey.tenant_id.isnot(None), ApiKey.tenant_id != "")
                    .distinct()
                )
                .scalars()
                .all()
            )
            quota_tenants = (
                session.execute(
                    select(LlmQuotaRule.tenant_id)
                    .where(
                        LlmQuotaRule.tenant_id.isnot(None),
                        LlmQuotaRule.tenant_id != "",
                    )
                    .distinct()
                )
                .scalars()
                .all()
            )

        merged = sorted(
            {
                t.strip()
                for t in (*policy_tenants, *key_tenants, *quota_tenants)
                if t and t.strip()
            }
        )
        return merged
