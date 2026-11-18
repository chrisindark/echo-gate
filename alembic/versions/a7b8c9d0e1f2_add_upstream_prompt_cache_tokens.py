"""Add upstream prompt-cache token counters to llm_usage_logs.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-18

The existing ``cache_read_tokens``/``cache_creation_tokens`` columns describe
Echo Gate's own semantic cache. Provider-native prompt caching (OpenAI implicit
caching, Anthropic ``cache_control``, Gemini implicit caching) needs its own
accounting so the two are never conflated in cost attribution. These columns
record what the upstream provider reported:

- ``upstream_cached_tokens`` — OpenAI/Gemini cached subset of prompt tokens.
- ``upstream_cache_read_input_tokens`` — Anthropic cache reads.
- ``upstream_cache_creation_input_tokens`` — Anthropic cache writes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "upstream_cached_tokens",
    "upstream_cache_read_input_tokens",
    "upstream_cache_creation_input_tokens",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column(
            "llm_usage_logs",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("llm_usage_logs", column)
