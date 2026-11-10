"""Distinguish discovery-only services and internal bundle planning."""

import sqlalchemy as sa
from alembic import op

revision = "0023_conversation_checks"
down_revision = "0022_review_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep all existing live flags; discovery/bundles stay unavailable to old workers."""
    with op.batch_alter_table("offers") as batch:
        batch.add_column(
            sa.Column("availability", sa.String(20), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("offer_type", sa.String(12), nullable=False, server_default="service")
        )
        batch.add_column(sa.Column("components", sa.JSON(), nullable=False, server_default="[]"))
    offers = sa.table(
        "offers",
        sa.column("service", sa.String()),
        sa.column("availability", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    op.execute(
        offers.update()
        .where(sa.func.lower(offers.c.service) == "ai voice agents")
        .values(availability="discovery_only", active=False)
    )


def downgrade() -> None:
    """Keep offers paused for older workers when removing catalogue fields."""
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("components")
        batch.drop_column("offer_type")
        batch.drop_column("availability")
