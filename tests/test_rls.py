"""Postgres Row-Level Security integration tests.

These require a real Postgres with the baseline migration applied. They are
skipped automatically when the configured database is unreachable or the schema
is missing, so the default test run stays hermetic.

Run against a local Postgres:

    DATABASE_URL=postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate \
        poetry run pytest tests/test_rls.py -v
"""

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.exc import DatabaseError

from app.core.config import config
from app.core.database import SessionLocal
from app.core.rls import rls_context, system_rls_context
from app.modules.api_keys.api_keys_schema import ApiKeyCreate
from app.modules.api_keys.api_keys_service import ApiKeyService
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog

TENANT_A = "itest_rls_tenant_a"
TENANT_B = "itest_rls_tenant_b"


def _database_ready() -> bool:
    try:
        probe = create_engine(config.DATABASE_URL, connect_args={"connect_timeout": 2})
        with probe.connect() as connection:
            connection.execute(text("SELECT 1 FROM gateway_requests LIMIT 1"))
        probe.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_ready(),
    reason="Postgres with the baseline migration is not reachable",
)


def _cleanup() -> None:
    with system_rls_context():
        with SessionLocal() as session:
            session.execute(
                delete(GatewayRequestLog).where(
                    GatewayRequestLog.tenant_id.in_([TENANT_A, TENANT_B])
                )
            )
            session.commit()


def test_rows_are_stamped_and_isolated_per_tenant():
    _cleanup()
    try:
        with rls_context(TENANT_A):
            with SessionLocal() as session:
                session.add(
                    GatewayRequestLog(query_text="alpha", routing_decision="LLM")
                )
                session.commit()

        with rls_context(TENANT_B):
            with SessionLocal() as session:
                session.add(
                    GatewayRequestLog(query_text="beta", routing_decision="LLM")
                )
                session.commit()

        # Tenant A only ever sees its own row, stamped from the RLS context.
        with rls_context(TENANT_A):
            with SessionLocal() as session:
                rows = (
                    session.query(GatewayRequestLog)
                    .filter(GatewayRequestLog.query_text.in_(["alpha", "beta"]))
                    .all()
                )
        assert [row.query_text for row in rows] == ["alpha"]
        assert rows[0].tenant_id == TENANT_A

        # A trusted system context sees both tenants.
        with system_rls_context():
            with SessionLocal() as session:
                total = (
                    session.query(GatewayRequestLog)
                    .filter(GatewayRequestLog.query_text.in_(["alpha", "beta"]))
                    .count()
                )
        assert total == 2
    finally:
        _cleanup()


def test_no_context_is_fail_closed():
    _cleanup()
    try:
        with system_rls_context():
            with SessionLocal() as session:
                session.add(
                    GatewayRequestLog(
                        query_text="gamma",
                        routing_decision="LLM",
                        tenant_id=TENANT_A,
                    )
                )
                session.commit()

        # The anonymous default context must not see other tenants' rows.
        with rls_context(None):
            with SessionLocal() as session:
                visible = (
                    session.query(GatewayRequestLog)
                    .filter(GatewayRequestLog.query_text == "gamma")
                    .count()
                )
        assert visible == 0
    finally:
        _cleanup()


def test_cross_tenant_insert_is_rejected():
    _cleanup()
    try:
        with rls_context(TENANT_A):
            with SessionLocal() as session:
                session.add(
                    GatewayRequestLog(
                        query_text="sneaky",
                        routing_decision="LLM",
                        tenant_id=TENANT_B,
                    )
                )
                with pytest.raises(DatabaseError):
                    session.commit()
    finally:
        _cleanup()


def test_api_key_resolution_binds_tenant_and_user():
    service = ApiKeyService()
    record, plaintext = service.create_api_key(
        ApiKeyCreate(name="itest-key", tenant_id=TENANT_A, user_id="user-1")
    )
    try:
        identity = ApiKeyService().resolve_api_key(plaintext)
        assert identity is not None
        assert identity.tenant_id == TENANT_A
        assert identity.user_id == "user-1"

        assert service.revoke_api_key(record.id) is True
        # Cache is cleared on revoke, so the key no longer resolves.
        assert ApiKeyService().resolve_api_key(plaintext) is None
    finally:
        with system_rls_context():
            with SessionLocal() as session:
                session.execute(
                    delete(GatewayRequestLog).where(
                        GatewayRequestLog.tenant_id.in_([TENANT_A, TENANT_B])
                    )
                )
                session.execute(
                    text("DELETE FROM api_keys WHERE id = :id"), {"id": record.id}
                )
                session.commit()
