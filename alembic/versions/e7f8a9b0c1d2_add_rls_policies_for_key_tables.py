"""Add RLS policies for admin_api_keys and api_keys.

Revision ID: e7f8a9b0c1d2
Revises: a7b8c9d0e1f2
Create Date: 2026-11-21

``admin_api_keys`` and ``api_keys`` are identity tables consulted before a tenant
context exists. In managed PostgreSQL environments like Supabase, Row-Level
Security is enabled by default across all tables in the ``public`` schema.
Without explicit policies for the non-owner application role (``DB_APP_ROLE``),
Postgres defaults to denying all access (violates row-level security policy).

This migration ensures that when RLS is enabled on these tables, the application
role (``echo_gate_app``) is granted explicit access, while preserving table-level
isolation from public/unauthenticated roles.
"""

import os
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")


def upgrade() -> None:
    # Ensure RLS policies exist for the application role on key identity tables.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'admin_api_keys' AND policyname = 'admin_api_keys_app_policy'
            ) THEN
                CREATE POLICY admin_api_keys_app_policy ON admin_api_keys
                    FOR ALL
                    TO "{_ROLE}"
                    USING (true)
                    WITH CHECK (true);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'api_keys' AND policyname = 'api_keys_app_policy'
            ) THEN
                CREATE POLICY api_keys_app_policy ON api_keys
                    FOR ALL
                    TO "{_ROLE}"
                    USING (true)
                    WITH CHECK (true);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS admin_api_keys_app_policy ON admin_api_keys;")
    op.execute("DROP POLICY IF EXISTS api_keys_app_policy ON api_keys;")
