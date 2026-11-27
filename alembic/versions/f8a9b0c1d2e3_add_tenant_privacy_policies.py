"""Add tenant_privacy_policies table with Postgres Row-Level Security (RLS).

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-27

Stores admin-managed per-tenant restrictions for semantic caching,
zero-retention mode, Stage 0 PII scrubbing, and guardrails. RLS restricts
non-system reads/writes to the caller's ``app.current_tenant`` while admin
operations enter ``system_rls_context()``.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")


def upgrade() -> None:
    op.create_table(
        "tenant_privacy_policies",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column(
            "semantic_cache_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "zero_retention",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "pii_scrubbing_mode",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column(
            "guardrails_mode",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_tenant_privacy_policies_created_at",
        "tenant_privacy_policies",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_privacy_policies_tenant_id",
        "tenant_privacy_policies",
        ["tenant_id"],
        unique=True,
    )
    op.create_index(
        "ix_tenant_privacy_policies_is_active",
        "tenant_privacy_policies",
        ["is_active"],
        unique=False,
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_catalog.pg_roles WHERE rolname = '{_APP_ROLE}'
            ) THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_privacy_policies TO "{_APP_ROLE}";
                GRANT USAGE, SELECT ON SEQUENCE tenant_privacy_policies_id_seq TO "{_APP_ROLE}";
            END IF;
        END
        $$;
        """
    )

    op.execute("ALTER TABLE tenant_privacy_policies ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenant_privacy_policies FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_privacy_policies_rls_policy ON tenant_privacy_policies
          USING (
            current_setting('app.rls_system', true) = 'on'
            OR tenant_id = current_setting('app.current_tenant', true)
          )
          WITH CHECK (
            current_setting('app.rls_system', true) = 'on'
            OR tenant_id = current_setting('app.current_tenant', true)
          );
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_privacy_policies_rls_policy ON tenant_privacy_policies;"
    )
    op.drop_index(
        "ix_tenant_privacy_policies_is_active",
        table_name="tenant_privacy_policies",
    )
    op.drop_index(
        "ix_tenant_privacy_policies_tenant_id",
        table_name="tenant_privacy_policies",
    )
    op.drop_index(
        "ix_tenant_privacy_policies_created_at",
        table_name="tenant_privacy_policies",
    )
    op.drop_table("tenant_privacy_policies")
