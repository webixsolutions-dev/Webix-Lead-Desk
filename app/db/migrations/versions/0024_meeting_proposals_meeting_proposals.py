"""meeting proposals"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_meeting_proposals"
down_revision: str | None = "0023_conversation_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "meeting_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("booking_id", "version"),
    )
    op.create_index(
        op.f("ix_meeting_notes_booking_id"), "meeting_notes", ["booking_id"], unique=False
    )
    op.create_table(
        "meeting_packs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("booking_id", "booking_version"),
    )
    op.create_index(
        op.f("ix_meeting_packs_booking_id"), "meeting_packs", ["booking_id"], unique=False
    )
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_reference", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["meeting_notes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("booking_id", "version"),
    )
    op.create_index(op.f("ix_proposals_booking_id"), "proposals", ["booking_id"], unique=False)
    op.create_index(op.f("ix_proposals_state"), "proposals", ["state"], unique=False)
    op.create_table(
        "proposal_followups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "day"),
    )
    op.create_index(
        op.f("ix_proposal_followups_due_at"), "proposal_followups", ["due_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_followups_proposal_id"),
        "proposal_followups",
        ["proposal_id"],
        unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("meeting_notes", "meeting_packs", "proposals", "proposal_followups"):
            op.execute("ALTER TABLE " + table + " ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_proposal_followups_proposal_id"), table_name="proposal_followups")
    op.drop_index(op.f("ix_proposal_followups_due_at"), table_name="proposal_followups")
    op.drop_table("proposal_followups")
    op.drop_index(op.f("ix_proposals_state"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_booking_id"), table_name="proposals")
    op.drop_table("proposals")
    op.drop_index(op.f("ix_meeting_packs_booking_id"), table_name="meeting_packs")
    op.drop_table("meeting_packs")
    op.drop_index(op.f("ix_meeting_notes_booking_id"), table_name="meeting_notes")
    op.drop_table("meeting_notes")
