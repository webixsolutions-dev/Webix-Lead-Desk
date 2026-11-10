"""Review-first outreach delegation and owner-confirmed bookings."""

import sqlalchemy as sa
from alembic import op

revision = "0022_review_workflow"
down_revision = "0021_reusable_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep old approvals manual and preserve sending defaults."""
    with op.batch_alter_table("draft_approvals") as batch:
        batch.add_column(sa.Column("source", sa.String(16), nullable=False, server_default="owner"))
        batch.add_column(sa.Column("policy_version", sa.Integer(), nullable=True))
    op.create_table(
        "bookings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("contact", sa.String(254), nullable=False),
        sa.Column("host", sa.String(160), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("location", sa.String(2000), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_by", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("company_id", "starts_at", "state"):
        op.create_index("ix_bookings_" + column, "bookings", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE bookings ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Remove booking records only on an explicit operator rollback."""
    op.drop_table("bookings")
    with op.batch_alter_table("draft_approvals") as batch:
        batch.drop_column("source")
        batch.drop_column("policy_version")
