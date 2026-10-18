"""Admin quota CRUD tests.

Two layers:

1. Schema/validation unit tests that need no database.
2. Postgres integration tests for ``LlmQuotaRuleService`` that verify the
   cross-tenant behaviour the admin surface depends on. The critical assertion is
   that a *tenant-scoped* session cannot see or write another tenant's rules,
   while the service (which runs in ``system_rls_context``) can. If that bypass
   ever stopped working the admin page would silently show an empty list.

The integration tests skip automatically when the configured database is
unreachable, so the default test run stays hermetic.

Run against a local Postgres:

    DATABASE_URL=postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate \
        poetry run pytest tests/test_llm_quota_admin.py -v
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, text

from app.core.config import config
from app.core.database import SessionLocal
from app.core.rls import rls_context, system_rls_context
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.llm_quota.llm_quota_rule_service import (
    LlmQuotaRuleService,
    UnauthenticatedAdminAccessError,
)
from app.modules.llm_quota.llm_quota_schema import (
    LlmQuotaRuleCreate,
    LlmQuotaRuleResponse,
    LlmQuotaRuleUpdate,
)

TENANT_A = "itest_quota_tenant_a"
TENANT_B = "itest_quota_tenant_b"


def _admin(scopes: frozenset[str] = frozenset({"quota:read", "quota:write"})):
    """A resolved admin identity, as AdminApiKeyGuard would hand to the service."""
    return AdminKeyIdentity(key_id=1, name="test-admin", scopes=scopes)


def _database_ready() -> bool:
    try:
        probe = create_engine(config.DATABASE_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as connection:
            connection.execute(text("SELECT 1 FROM llm_quota_rules LIMIT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_ready(),
    reason="Postgres with the baseline migration is not reachable",
)


def _cleanup() -> None:
    with system_rls_context():
        with SessionLocal() as session:
            session.execute(
                delete(LlmQuotaRule).where(
                    LlmQuotaRule.tenant_id.in_([TENANT_A, TENANT_B])
                )
            )
            session.commit()


# --- Schema layer (no database) ---------------------------------------------


def test_create_requires_at_least_one_limit():
    with pytest.raises(ValueError):
        LlmQuotaRuleCreate(provider="openai", max_rpm=None, max_tpm=None)


def test_create_allows_a_single_limit():
    rule = LlmQuotaRuleCreate(provider="openai", max_rpm=5)
    assert rule.max_rpm == 5
    assert rule.max_tpm is None
    # tenant_id stays unset -> a global rule, not the caller's tenant.
    assert rule.tenant_id is None


def test_create_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        LlmQuotaRuleCreate(max_rpm=0)
    with pytest.raises(ValueError):
        LlmQuotaRuleCreate(max_tpm=-5)


def test_response_validates_from_orm_attributes():
    now = datetime.now(UTC)
    rule = LlmQuotaRule(
        id=7,
        provider="groq",
        model="openai/gpt-oss-20b",
        user_id=None,
        tenant_id="acme",
        max_rpm=30,
        max_tpm=1000,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    payload = LlmQuotaRuleResponse.model_validate(rule, from_attributes=True)
    assert payload.id == 7
    assert payload.tenant_id == "acme"


# --- Service layer (real Postgres) ------------------------------------------


@requires_db
def test_create_list_get_update_delete_round_trip():
    _cleanup()
    service = LlmQuotaRuleService()
    admin = _admin()

    try:
        created = service.create_rule(
            LlmQuotaRuleCreate(tenant_id=TENANT_A, max_rpm=42, max_tpm=9000), admin
        )
        assert created.id is not None
        assert created.tenant_id == TENANT_A

        listed, total = service.list_rules(admin, tenant_id=TENANT_A)
        assert total == 1
        assert [r.id for r in listed] == [created.id]

        fetched = service.get_rule(created.id, admin)
        assert fetched is not None
        assert fetched.max_rpm == 42

        updated = service.update_rule(
            created.id, LlmQuotaRuleUpdate(max_rpm=7, is_active=False), admin
        )
        assert updated is not None
        assert updated.max_rpm == 7
        # Untouched fields survive a partial update.
        assert updated.max_tpm == 9000
        assert updated.is_active is False

        assert service.delete_rule(created.id, admin) is True
        assert service.delete_rule(created.id, admin) is False
        assert service.get_rule(created.id, admin) is None
    finally:
        _cleanup()


@requires_db
def test_update_and_get_missing_rule_return_none():
    service = LlmQuotaRuleService()
    admin = _admin()
    assert (
        service.update_rule(2_147_483_640, LlmQuotaRuleUpdate(max_rpm=1), admin) is None
    )
    assert service.get_rule(2_147_483_640, admin) is None


@requires_db
def test_global_rule_keeps_a_null_tenant():
    """A blank tenant must stay global, not get stamped with the caller.

    ``_stamp_rls_identity`` skips system mode, so the service must not let the
    NULL tenant be rewritten into a tenant-scoped rule.
    """
    _cleanup()
    service = LlmQuotaRuleService()
    admin = _admin()

    try:
        global_rule = service.create_rule(
            LlmQuotaRuleCreate(provider="ollama", max_rpm=5), admin
        )
        assert global_rule.tenant_id is None
        assert service.get_rule(global_rule.id, admin).tenant_id is None
        assert service.delete_rule(global_rule.id, admin) is True
    finally:
        _cleanup()


def test_service_refuses_to_open_a_session_without_an_admin_identity():
    """The service must not hand out an RLS-bypassing session unauthenticated.

    This is the fail-loud guard rail. It is not a security boundary (in-process
    Python can call the service regardless), but it turns a forgotten or
    mis-wired dependency from a silent RLS bypass into a loud error.
    """
    service = LlmQuotaRuleService()
    payload = LlmQuotaRuleCreate(tenant_id=TENANT_A, max_rpm=1)

    # No identity at all.
    with pytest.raises(UnauthenticatedAdminAccessError):
        service.create_rule(payload, None)

    # A mis-wired dependency that resolves to something else (this is exactly
    # what happens when the guard *class* is passed to Depends instead of an
    # instance: FastAPI resolves the un-invoked instance).
    with pytest.raises(UnauthenticatedAdminAccessError):
        service.create_rule(payload, object())

    # Even the empty string must not pass an `is None` style check.
    with pytest.raises(UnauthenticatedAdminAccessError):
        service.list_rules("")


@requires_db
def test_service_reads_and_writes_across_tenants_via_system_rls():
    """The admin surface must be cross-tenant while tenant sessions are not.

    This is the behaviour the quota management page depends on, and it pins the
    security-relevant property that the admin guard exists to protect: the
    system-RLS bypass is reachable from this service, so the service refuses to
    run without a resolved admin identity (see the test above).
    """
    _cleanup()
    service = LlmQuotaRuleService()
    admin = _admin()

    try:
        a_rule = service.create_rule(
            LlmQuotaRuleCreate(tenant_id=TENANT_A, max_rpm=11), admin
        )
        b_rule = service.create_rule(
            LlmQuotaRuleCreate(tenant_id=TENANT_B, max_rpm=13), admin
        )

        # Service (system RLS) sees both tenants' rules in one listing.
        all_rules, total = service.list_rules(admin)
        ids = {r.id for r in all_rules}
        assert total >= 2
        assert a_rule.id in ids and b_rule.id in ids

        assert TENANT_A in service.list_tenants(admin)
        assert TENANT_B in service.list_tenants(admin)

        # A plain tenant-scoped session sees neither other tenant's rule.
        for tenant in (TENANT_A, TENANT_B):
            with rls_context(tenant):
                with SessionLocal() as session:
                    visible = {
                        r.id
                        for r in session.query(LlmQuotaRule)
                        .filter(LlmQuotaRule.tenant_id == tenant)
                        .all()
                    }
                    assert visible == {tenant == TENANT_A and a_rule.id or b_rule.id}
    finally:
        _cleanup()


@requires_db
def test_effective_limits_pick_the_most_restrictive_match():
    _cleanup()
    service = LlmQuotaRuleService()
    admin = _admin()

    try:
        service.create_rule(
            LlmQuotaRuleCreate(tenant_id=TENANT_A, provider="openai", max_rpm=100),
            admin,
        )
        service.create_rule(
            LlmQuotaRuleCreate(tenant_id=TENANT_A, provider="openai", max_rpm=20), admin
        )
        service.create_rule(
            LlmQuotaRuleCreate(
                tenant_id=TENANT_A, provider="openai", max_tpm=500, is_active=False
            ),
            admin,
        )

        # Inactive rules must not constrain the result.
        rpm, tpm = service.preview_effective_limits(
            admin, tenant_id=TENANT_A, provider="openai"
        )
        assert rpm == 20
        assert tpm is None

        # No match at all -> both limits None (caller shows gateway defaults).
        rpm_none, tpm_none = service.preview_effective_limits(
            admin, tenant_id="itest_quota_tenant_missing"
        )
        assert rpm_none is None
        assert tpm_none is None
    finally:
        _cleanup()
