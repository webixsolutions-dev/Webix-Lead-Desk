"""official member posting and private media"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_linkedin_posts"
down_revision: str | None = "0017_linkedin_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "linkedin_oauth_states",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("digest"),
    )
    op.create_table(
        "linkedin_post_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_linkedin_post_media_profile_id"),
        "linkedin_post_media",
        ["profile_id"],
        unique=False,
    )
    op.create_table(
        "linkedin_posting_connections",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("member_name", sa.String(length=200), nullable=False),
        sa.Column("profile_url", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint("subject"),
    )
    op.create_table(
        "linkedin_posts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_urn", sa.String(length=200), nullable=False),
        sa.Column("published_url", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_linkedin_posts_due_at"), "linkedin_posts", ["due_at"], unique=False)
    op.create_index(
        op.f("ix_linkedin_posts_profile_id"), "linkedin_posts", ["profile_id"], unique=False
    )
    op.create_index(op.f("ix_linkedin_posts_state"), "linkedin_posts", ["state"], unique=False)
    op.create_table(
        "linkedin_post_media_chunks",
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["linkedin_post_media.id"],
        ),
        sa.PrimaryKeyConstraint("media_id", "number"),
    )
    op.create_table(
        "linkedin_post_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("post_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("body_key", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["linkedin_posts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "revision"),
    )
    op.create_index(
        op.f("ix_linkedin_post_revisions_body_key"),
        "linkedin_post_revisions",
        ["body_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_linkedin_post_revisions_post_id"),
        "linkedin_post_revisions",
        ["post_id"],
        unique=False,
    )
    op.create_table(
        "linkedin_post_approvals",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["linkedin_post_revisions.id"],
        ),
        sa.PrimaryKeyConstraint("revision_id"),
    )

    for table in ("linkedin_post_revisions", "linkedin_post_approvals"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_touch_mutation()"
            )
        else:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'immutable post approval'); END"
                )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("linkedin_post_approvals")
    op.drop_index(op.f("ix_linkedin_post_revisions_post_id"), table_name="linkedin_post_revisions")
    op.drop_index(op.f("ix_linkedin_post_revisions_body_key"), table_name="linkedin_post_revisions")
    op.drop_table("linkedin_post_revisions")
    op.drop_table("linkedin_post_media_chunks")
    op.drop_index(op.f("ix_linkedin_posts_state"), table_name="linkedin_posts")
    op.drop_index(op.f("ix_linkedin_posts_profile_id"), table_name="linkedin_posts")
    op.drop_index(op.f("ix_linkedin_posts_due_at"), table_name="linkedin_posts")
    op.drop_table("linkedin_posts")
    op.drop_table("linkedin_posting_connections")
    op.drop_index(op.f("ix_linkedin_post_media_profile_id"), table_name="linkedin_post_media")
    op.drop_table("linkedin_post_media")
    op.drop_table("linkedin_oauth_states")
