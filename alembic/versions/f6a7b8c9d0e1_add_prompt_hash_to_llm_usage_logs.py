"""Add prompt_hash to llm_usage_logs for prompt-level cost attribution.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-04

Stores a SHA-256 of the *system* prompt only. A system prompt usually identifies
the downstream application/workflow, so grouping usage rows by this column gives
per-app cost attribution without requiring explicit caller tagging. The hash is
opaque and one-way, so no prompt text is duplicated.

Nullable on purpose: rows written before this revision have no hash until the
backfill runs (`python -m app.scripts.backfill_prompt_hashes`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_llm_usage_logs_prompt_hash"


def upgrade() -> None:
    op.add_column(
        "llm_usage_logs",
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(_INDEX, "llm_usage_logs", ["prompt_hash"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="llm_usage_logs")
    op.drop_column("llm_usage_logs", "prompt_hash")
