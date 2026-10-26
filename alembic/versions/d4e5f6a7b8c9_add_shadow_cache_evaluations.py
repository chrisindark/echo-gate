"""Add shadow_cache_evaluations for the observation-only cache pipeline.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-26

Written only when ``CACHE_MODE=shadow``: one row per request that replayed the
full cache pipeline out of band, recording whether a match would have occurred,
which layer matched, the scores, and whether the cached answer was equivalent to
the fresh one. Tenant-scoped by Row-Level Security, matching ``gateway_requests``.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Resolved exactly as the baseline migration does so the grants match.
_ROLE = os.getenv("DB_APP_ROLE", "echo_gate_app")

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


def upgrade() -> None:
    op.create_table(
        "shadow_cache_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("gateway_requests.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("exact_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("tenant_id", sa.String(length=100), nullable=True, index=True),
        sa.Column("user_id", sa.String(length=100), nullable=True, index=True),
        sa.Column("model", sa.String(length=100), nullable=True, index=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("would_have_hit", sa.Boolean(), nullable=False, index=True),
        sa.Column("match_type", sa.String(length=50), nullable=False, index=True),
        sa.Column("point_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("match_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("rerank_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("score_breakdown", sa.JSON(), nullable=True),
        sa.Column("cached_response_text", sa.Text(), nullable=True),
        sa.Column("actual_response_text", sa.Text(), nullable=True),
        sa.Column("is_equivalent", sa.Boolean(), nullable=True, index=True),
        sa.Column("intent", sa.String(length=100), nullable=True, index=True),
        sa.Column("entities", sa.JSON(), nullable=True),
        sa.Column("latency_breakdown", sa.JSON(), nullable=True),
        sa.Column("provider_latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_lookup_latency_ms", sa.Integer(), nullable=True),
        sa.Column("projected_latency_saved_ms", sa.Integer(), nullable=True),
    )

    op.execute("ALTER TABLE shadow_cache_evaluations ENABLE ROW LEVEL SECURITY;")
    op.execute(
        _TENANT_POLICY.format(
            name="shadow_cache_evaluations_tenant_isolation",
            table="shadow_cache_evaluations",
        )
    )

    # The application connects as a non-owner role, so it needs explicit DML
    # grants. The baseline set ALTER DEFAULT PRIVILEGES, but re-granting is cheap
    # and keeps this migration correct if that ever changes.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE shadow_cache_evaluations "
        f'TO "{_ROLE}";'
    )
    op.execute(
        f'GRANT USAGE, SELECT ON SEQUENCE shadow_cache_evaluations_id_seq TO "{_ROLE}";'
    )


def downgrade() -> None:
    op.drop_table("shadow_cache_evaluations")
