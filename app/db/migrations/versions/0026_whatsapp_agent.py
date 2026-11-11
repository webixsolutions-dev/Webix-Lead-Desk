"""Opt-in native WhatsApp agent pairing, notices and replay fences."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_whatsapp_agent"
down_revision: str | None = "0025_optional_owner_mfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create inactive-until-configured integration records without contacting any provider."""
    op.create_table(
        "whatsapp_connections",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("share_details", sa.Boolean(), nullable=False),
        sa.Column("recipient", sa.String(100), nullable=False),
        sa.Column("pair_hash", sa.String(64), nullable=False),
        sa.Column("pair_expires", sa.DateTime(timezone=True)),
        sa.Column("offset", sa.Integer()),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll", sa.DateTime(timezone=True)),
        sa.Column("next_poll", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
    )
    op.create_table(
        "whatsapp_notices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("drafts.id")),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("code", sa.String(24), nullable=False, unique=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("message_id", sa.String(300), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation", "source_key"),
    )
    op.create_index("ix_whatsapp_notices_generation", "whatsapp_notices", ["generation"])
    op.create_table(
        "whatsapp_commands",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("whatsapp_connections", "whatsapp_notices", "whatsapp_commands"):
            op.execute("ALTER TABLE " + table + " ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Remove only WhatsApp integration records during explicit rollback."""
    op.drop_table("whatsapp_commands")
    op.drop_table("whatsapp_notices")
    op.drop_table("whatsapp_connections")
