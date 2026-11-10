"""Optional owner authenticator and revocable device descriptions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_optional_owner_mfa"
down_revision: str | None = "0024_meeting_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add disabled-by-default factors; never change existing sign-in settings."""
    op.add_column(
        "sessions", sa.Column("user_agent", sa.String(200), nullable=False, server_default="")
    )
    op.add_column("sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "owner_mfa",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("pending_secret", sa.Text(), nullable=True),
        sa.Column("pending_expires", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_session_hash", sa.String(64), nullable=True),
        sa.Column("last_counter", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "mfa_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_fingerprint", sa.String(64), nullable=False),
        sa.Column("mfa_version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_mfa_challenges_user_id", "mfa_challenges", ["user_id"])
    op.create_table(
        "mfa_recovery",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mfa_recovery_user_id", "mfa_recovery", ["user_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("owner_mfa", "mfa_challenges", "mfa_recovery"):
            op.execute("ALTER TABLE " + table + " ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Remove factor records only during an explicit rollback."""
    op.drop_table("mfa_recovery")
    op.drop_table("mfa_challenges")
    op.drop_table("owner_mfa")
    op.drop_column("sessions", "last_seen_at")
    op.drop_column("sessions", "user_agent")
