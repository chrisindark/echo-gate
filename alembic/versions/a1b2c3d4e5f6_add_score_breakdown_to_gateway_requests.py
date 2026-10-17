"""Add score_breakdown JSON column to gateway_requests.

Records the reranker's composite score decomposition (base cross-encoder score,
per-signal factors, and signal details) alongside each trace so cache-match
decisions can be diagnosed and calibrated from production traffic.

Revision ID: a1b2c3d4e5f6
Revises: 3f9c1a7b2d40
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "3f9c1a7b2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gateway_requests",
        sa.Column("score_breakdown", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gateway_requests", "score_breakdown")
