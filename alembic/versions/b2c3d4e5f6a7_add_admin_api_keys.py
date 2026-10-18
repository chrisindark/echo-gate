"""Add admin_api_keys table.

Admin API keys authenticate the cross-tenant admin surface
(`/api/v1/admin/*`) and are deliberately kept in their own table rather than
being an ``is_admin`` column on ``api_keys``:

* Tenant keys mint an identity and are creatable over HTTP. Admin keys mint
  *privilege*, so the only way to create one is the offline
  ``app.scripts.create_admin_key`` CLI. Keeping them in a separate table makes
  that structural instead of a convention someone can route around.
* Admin keys are not tenant identities. ``TenantContextMiddleware`` resolves any
  valid ``api_keys`` row into a tenant context; an admin key must never become
  one, so it must not live in that table.
* A SQL dump of ``api_keys`` then leaks tenant credentials only, not admin
  credentials.

RLS is intentionally NOT enabled, for the same reason as ``api_keys``: admin
key resolution happens before any tenant context exists, so the row must be
readable regardless of the active tenant. Access control is by key possession
and is enforced in ``app/core/guards.py``.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-18
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The application role is resolved from the environment exactly as the baseline
# migration does, so the new table grants match the existing ones.
_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")


def upgrade() -> None:
    op.create_table(
        "admin_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        # Human label for the key holder ("ops", "ci-deploy"). Not unique: the
        # same operator may legitimately hold more than one key.
        sa.Column("name", sa.String(100), nullable=False),
        # First chars of the plaintext, for display only. Never the full key.
        sa.Column("key_prefix", sa.String(16), nullable=False),
        # SHA-256 hex digest. The plaintext is shown exactly once at creation and
        # is never recoverable from this table.
        sa.Column("key_hash", sa.String(64), nullable=False),
        # Comma-separated scopes ("quota:read,quota:write"). "admin:*" grants all.
        sa.Column("scopes", sa.String(255), nullable=False),
        # Optional expiry; NULL means it does not expire on its own.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # Throttled by the service, not written per request (see admin_api_keys_service).
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_admin_api_keys_key_hash", "admin_api_keys", ["key_hash"], unique=True
    )
    op.create_index("ix_admin_api_keys_key_prefix", "admin_api_keys", ["key_prefix"])
    op.create_index("ix_admin_api_keys_is_active", "admin_api_keys", ["is_active"])
    op.create_index("ix_admin_api_keys_created_at", "admin_api_keys", ["created_at"])

    # The application connects as a non-owner role, so it needs explicit DML
    # grants. The baseline set ALTER DEFAULT PRIVILEGES, but re-granting is cheap
    # and keeps this migration correct if that ever changes.
    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE admin_api_keys TO "{_ROLE}";'
    )


def downgrade() -> None:
    op.drop_table("admin_api_keys")
