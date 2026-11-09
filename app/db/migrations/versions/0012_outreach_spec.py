"""Per-mailbox connections, explicit message kinds and verified contact routing."""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0012_outreach_spec"
down_revision: str | None = "0011_ai_batch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve historical messages while separating their purpose from their ordinal."""
    op.add_column("mailboxes", sa.Column("connection", sa.JSON(), nullable=True))
    op.add_column("campaigns", sa.Column("followup_days", sa.JSON(), nullable=True))
    op.add_column(
        "drafts", sa.Column("kind", sa.String(20), server_default="first_touch", nullable=False)
    )
    op.add_column(
        "drafts", sa.Column("sequence_step", sa.Integer(), server_default="0", nullable=False)
    )
    contact_columns: list[sa.Column[Any]] = [
        sa.Column("dns_result", sa.String(24)),
        sa.Column("dns_address", sa.String(254)),
        sa.Column("dns_checked_at", sa.DateTime(timezone=True)),
        sa.Column("allow_role", sa.Boolean(), server_default="0", nullable=False),
    ]
    for column in contact_columns:
        op.add_column("contacts", column)
    op.create_table(
        "local_team_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
    )
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, step, snapshot FROM drafts")).mappings():
        snapshot = (
            json.loads(row["snapshot"]) if isinstance(row["snapshot"], str) else row["snapshot"]
        )
        kind = (
            "concept"
            if snapshot.get("attachments")
            else "follow_up"
            if row["step"]
            else "first_touch"
        )
        bind.execute(
            sa.text("UPDATE drafts SET kind=:kind, sequence_step=:step WHERE id=:id"),
            {"kind": kind, "step": row["step"] if kind == "follow_up" else 0, "id": row["id"]},
        )


def downgrade() -> None:
    """Remove new review metadata only through an explicit rollback."""
    op.drop_table("local_team_members")
    for table, columns in {
        "contacts": ["dns_result", "dns_address", "dns_checked_at", "allow_role"],
        "drafts": ["kind", "sequence_step"],
        "campaigns": ["followup_days"],
        "mailboxes": ["connection"],
    }.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
