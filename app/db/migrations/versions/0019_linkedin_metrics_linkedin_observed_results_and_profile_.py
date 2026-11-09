"""LinkedIn observed results and profile review"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_linkedin_metrics"
down_revision: str | None = "0018_linkedin_posts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "linkedin_profile_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("ssi", sa.Integer(), nullable=True),
        sa.Column("warning", sa.String(length=1000), nullable=False),
        sa.Column("restricted", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_linkedin_profile_observations_profile_id"),
        "linkedin_profile_observations",
        ["profile_id"],
        unique=False,
    )
    op.create_table(
        "linkedin_profile_risk_reviews",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("review_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("profile_id"),
    )
    op.create_table(
        "linkedin_post_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["linkedin_posts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_linkedin_post_observations_post_id"),
        "linkedin_post_observations",
        ["post_id"],
        unique=False,
    )
    op.add_column(
        "linkedin_posts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )

    for table in ("linkedin_profile_observations", "linkedin_post_observations"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_touch_mutation()"
            )
        else:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'immutable analytics observation'); END"
                )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("linkedin_posts", "published_at")
    op.drop_index(
        op.f("ix_linkedin_post_observations_post_id"), table_name="linkedin_post_observations"
    )
    op.drop_table("linkedin_post_observations")
    op.drop_table("linkedin_profile_risk_reviews")
    op.drop_index(
        op.f("ix_linkedin_profile_observations_profile_id"),
        table_name="linkedin_profile_observations",
    )
    op.drop_table("linkedin_profile_observations")
