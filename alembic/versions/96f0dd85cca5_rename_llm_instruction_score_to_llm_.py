"""rename_llm_instruction_score_to_llm_entailment_score

Revision ID: 96f0dd85cca5
Revises: bb35f2041f68
Create Date: 2026-10-07 20:08:10.502755

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "96f0dd85cca5"
down_revision: str | Sequence[str] | None = "bb35f2041f68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("gateway_requests") as batch_op:
        batch_op.alter_column(
            "llm_instruction_score",
            new_column_name="llm_entailment_score",
            existing_type=sa.Numeric(precision=5, scale=4),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("gateway_requests") as batch_op:
        batch_op.alter_column(
            "llm_entailment_score",
            new_column_name="llm_instruction_score",
            existing_type=sa.Numeric(precision=5, scale=4),
            existing_nullable=True,
        )
