"""Reusable service offers, optional quote pricing and service-scoped proof."""

import sqlalchemy as sa
from alembic import op

revision = "0021_reusable_offers"
down_revision = "0020_hosted_supabase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve existing CAD amounts and leave old proof unassigned until reviewed."""
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("name", sa.String(120), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("pricing_mode", sa.String(12), nullable=False, server_default="fixed")
        )
        batch.add_column(sa.Column("currency", sa.String(3), nullable=False, server_default="CAD"))
        batch.add_column(
            sa.Column("billing_period", sa.String(12), nullable=False, server_default="monthly")
        )
        batch.alter_column("setup_cad_cents", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("recurring_cad_cents", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("proof_cards") as batch:
        batch.add_column(sa.Column("services", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("industries", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("attribution", sa.String(120), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    """Refuse a rollback that would turn a custom quote or foreign amount into CAD."""
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM offers WHERE setup_cad_cents IS NULL "
            "OR recurring_cad_cents IS NULL OR currency != 'CAD'"
        )
    ):
        raise RuntimeError("Export and review quote/non-CAD offers before downgrading")
    with op.batch_alter_table("proof_cards") as batch:
        for column in ("services", "industries", "attribution", "version"):
            batch.drop_column(column)
    with op.batch_alter_table("offers") as batch:
        for column in ("name", "pricing_mode", "currency", "billing_period"):
            batch.drop_column(column)
        batch.alter_column("setup_cad_cents", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("recurring_cad_cents", existing_type=sa.Integer(), nullable=False)
