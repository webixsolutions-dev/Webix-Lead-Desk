"""Persist acknowledged Batch API submissions for safe polling."""

import sqlalchemy as sa
from alembic import op

revision = "0011_ai_batch"
down_revision = "0010_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add optional provider state without changing existing call reservations."""
    op.add_column("ai_calls", sa.Column("batch", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove optional batch state only during an explicit rollback."""
    with op.batch_alter_table("ai_calls") as batch:
        batch.drop_column("batch")
