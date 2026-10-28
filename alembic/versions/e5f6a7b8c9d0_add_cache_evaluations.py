"""Add cache_evaluations for the asynchronous shadow audit loop.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-28

Written on sampled semantic cache hits in CACHE_MODE=active: captures ground truth
comparisons between cached answers and fresh provider completions. Enables instant
eviction of false-positive matches and data-driven threshold calibration. Tenant-scoped
by Row-Level Security, matching gateway_requests.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
        "cache_evaluations",
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
        sa.Column("match_type", sa.String(length=50), nullable=True, index=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("cached_answer", sa.Text(), nullable=True),
        sa.Column("fresh_answer", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(length=100), nullable=True, index=True),
        sa.Column("vector_similarity", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("rerank_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("is_equivalent", sa.Boolean(), nullable=True, index=True),
        sa.Column("point_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("latency_saved_ms", sa.Integer(), nullable=True),
        sa.Column("evicted", sa.Boolean(), nullable=False, default=False, index=True),
    )

    op.execute("ALTER TABLE cache_evaluations ENABLE ROW LEVEL SECURITY;")
    op.execute(
        _TENANT_POLICY.format(
            name="cache_evaluations_tenant_isolation",
            table="cache_evaluations",
        )
    )

    op.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE cache_evaluations TO "{_ROLE}";'
    )
    op.execute(
        f'GRANT USAGE, SELECT ON SEQUENCE cache_evaluations_id_seq TO "{_ROLE}";'
    )


def downgrade() -> None:
    op.drop_table("cache_evaluations")
