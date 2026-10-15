"""Postgres baseline: all tables, RLS policies, and the application role.

Revision ID: 3f9c1a7b2d40
Revises:
Create Date: 2026-10-15

Replaces the previous SQLite-generated revision chain with a single Postgres
baseline. Every multi-tenant table has Row-Level Security enabled, and a
non-owner application role is provisioned (RLS is bypassed by table owners and
superusers, so the app must not connect as the migration role).
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f9c1a7b2d40"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")
_APP_PASSWORD = os.getenv("DB_APP_PASSWORD", "echo_gate_app").replace("'", "''")

# Policies: trusted background jobs run in system mode ('app.rls_system' = 'on').
_TENANT_POLICY = """
CREATE POLICY {name} ON {table}
  USING (
    current_setting('app.rls_system', true) = 'on'
    OR tenant_id = current_setting('app.current_tenant', true)
  )
  WITH CHECK (
    current_setting('app.rls_system', true) = 'on'
    OR tenant_id = current_setting('app.current_tenant', true)
  );
"""

# Global quota rules (tenant_id IS NULL) must stay visible to every tenant.
_QUOTA_POLICY = """
CREATE POLICY {name} ON {table}
  USING (
    current_setting('app.rls_system', true) = 'on'
    OR tenant_id IS NULL
    OR tenant_id = current_setting('app.current_tenant', true)
  )
  WITH CHECK (
    current_setting('app.rls_system', true) = 'on'
    OR tenant_id IS NULL
    OR tenant_id = current_setting('app.current_tenant', true)
  );
"""

# Conversation data is scoped by tenant and, when a user is present, by user.
_USER_POLICY = """
CREATE POLICY {name} ON {table}
  USING (
    current_setting('app.rls_system', true) = 'on'
    OR (
      tenant_id = current_setting('app.current_tenant', true)
      AND (
        current_setting('app.current_user', true) = ''
        OR user_id = current_setting('app.current_user', true)
      )
    )
  )
  WITH CHECK (
    current_setting('app.rls_system', true) = 'on'
    OR (
      tenant_id = current_setting('app.current_tenant', true)
      AND (
        current_setting('app.current_user', true) = ''
        OR user_id = current_setting('app.current_user', true)
      )
    )
  );
"""

_TENANT_TABLES = ("gateway_requests", "llm_usage_logs")
_USER_TABLES = ("conversations", "messages")


def upgrade() -> None:
    _create_tables()
    _create_indexes()
    _provision_app_role()
    _enable_rls()


def _create_tables() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "llm_usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_name", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("caller_service_name", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=True),
        sa.Column("session_id", sa.String(length=100), nullable=True),
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "cost_calculated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("is_success", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("finish_reason", sa.String(length=50), nullable=True),
        sa.Column("temperature", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=True),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
    )

    op.create_table(
        "llm_quota_rules",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=True),
        sa.Column("max_rpm", sa.Integer(), nullable=True),
        sa.Column("max_tpm", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
    )

    op.create_table(
        "gateway_requests",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("routing_decision", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("point_id", sa.String(length=36), nullable=True),
        sa.Column("exact_hash", sa.String(length=64), nullable=True),
        sa.Column("intent", sa.String(length=100), nullable=True),
        sa.Column("core_operation", sa.String(length=255), nullable=True),
        sa.Column("core_subject", sa.String(length=255), nullable=True),
        sa.Column("subject_modifier", sa.String(length=255), nullable=True),
        sa.Column("action_modifier", sa.String(length=255), nullable=True),
        sa.Column("rerank_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider_latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_lookup_latency_ms", sa.Integer(), nullable=True),
        sa.Column("latency_breakdown", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=True),
        sa.Column("session_id", sa.String(length=100), nullable=True),
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
        sa.Column("evaluation_status", sa.String(length=20), nullable=True),
        sa.Column(
            "llm_relevance_score", sa.Numeric(precision=5, scale=4), nullable=True
        ),
        sa.Column(
            "llm_contradiction_score", sa.Numeric(precision=5, scale=4), nullable=True
        ),
        sa.Column(
            "llm_entailment_score", sa.Numeric(precision=5, scale=4), nullable=True
        ),
        sa.Column("is_false_positive", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )


def _create_indexes() -> None:
    indexes = {
        "api_keys": [
            ("tenant_id", False),
            ("user_id", False),
            ("key_prefix", False),
            ("is_active", False),
            ("created_at", False),
            ("key_hash", True),
        ],
        "llm_usage_logs": [
            ("created_at", False),
            ("service_name", False),
            ("model", False),
            ("caller_service_name", False),
            ("user_id", False),
            ("tenant_id", False),
            ("session_id", False),
            ("conversation_id", False),
            ("cost_calculated", False),
            ("is_success", False),
        ],
        "llm_quota_rules": [
            ("created_at", False),
            ("provider", False),
            ("model", False),
            ("user_id", False),
            ("tenant_id", False),
        ],
        "gateway_requests": [
            ("created_at", False),
            ("routing_decision", False),
            ("point_id", False),
            ("exact_hash", False),
            ("intent", False),
            ("core_operation", False),
            ("core_subject", False),
            ("subject_modifier", False),
            ("action_modifier", False),
            ("provider_latency_ms", False),
            ("user_id", False),
            ("tenant_id", False),
            ("session_id", False),
            ("conversation_id", False),
            ("evaluation_status", False),
            ("is_false_positive", False),
        ],
        "conversations": [("tenant_id", False), ("user_id", False)],
        "messages": [
            ("conversation_id", False),
            ("tenant_id", False),
            ("user_id", False),
        ],
    }
    for table, columns in indexes.items():
        for column, unique in columns:
            op.create_index(f"ix_{table}_{column}", table, [column], unique=unique)


def _provision_app_role() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = '{_APP_ROLE}'
            ) THEN
                CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{_APP_PASSWORD}';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO {_APP_ROLE}', current_database()
            );
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE};")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {_APP_ROLE};"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE};")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE};"
    )


def _enable_rls() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(_TENANT_POLICY.format(name=f"{table}_tenant_isolation", table=table))

    op.execute("ALTER TABLE llm_quota_rules ENABLE ROW LEVEL SECURITY;")
    op.execute(
        _QUOTA_POLICY.format(
            name="llm_quota_rules_tenant_isolation", table="llm_quota_rules"
        )
    )

    for table in _USER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(_USER_POLICY.format(name=f"{table}_tenant_isolation", table=table))

    # api_keys is an identity table read before any tenant context exists, so it
    # is intentionally left without RLS; access is controlled by role grants.


def downgrade() -> None:
    for table in ("messages", "conversations"):
        op.drop_table(table)
    op.drop_table("gateway_requests")
    op.drop_table("llm_quota_rules")
    op.drop_table("llm_usage_logs")
    op.drop_table("api_keys")
    op.execute(f"DROP ROLE IF EXISTS {_APP_ROLE};")
