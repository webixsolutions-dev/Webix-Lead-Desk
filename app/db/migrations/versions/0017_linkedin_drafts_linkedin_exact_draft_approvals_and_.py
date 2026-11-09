"""linkedin exact draft approvals and monthly AI categories"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_linkedin_drafts"
down_revision: str | None = "0016_linkedin_planner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "person_pipeline",
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=False),
        sa.Column("next_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
        ),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.create_table(
        "linkedin_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["linkedin_tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "revision"),
    )
    op.create_index(
        op.f("ix_linkedin_drafts_task_id"), "linkedin_drafts", ["task_id"], unique=False
    )
    op.create_table(
        "linkedin_approvals",
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["linkedin_drafts.id"],
        ),
        sa.PrimaryKeyConstraint("draft_id"),
    )
    op.add_column(
        "ai_calls",
        sa.Column("category", sa.String(length=24), server_default="reserve", nullable=False),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE ai_calls ADD COLUMN company_id VARCHAR(36) REFERENCES companies(id)"
        )
    else:
        op.add_column("ai_calls", sa.Column("company_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_ai_calls_company", "ai_calls", "companies", ["company_id"], ["id"]
        )
    op.create_index(op.f("ix_ai_calls_company_id"), "ai_calls", ["company_id"], unique=False)
    _install_guards()


def downgrade() -> None:
    """Revert the migration."""
    _drop_guards()
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_ai_calls_company", "ai_calls", type_="foreignkey")
    op.drop_index(op.f("ix_ai_calls_company_id"), table_name="ai_calls")
    op.drop_column("ai_calls", "company_id")
    op.drop_column("ai_calls", "category")
    op.drop_table("linkedin_approvals")
    op.drop_index(op.f("ix_linkedin_drafts_task_id"), table_name="linkedin_drafts")
    op.drop_table("linkedin_drafts")
    op.drop_table("person_pipeline")


def _install_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION guard_linkedin_draft() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'immutable draft'; END IF; "
            "IF OLD.task_id IS DISTINCT FROM NEW.task_id OR OLD.revision IS "
            "DISTINCT FROM NEW.revision "
            "OR OLD.snapshot::text IS DISTINCT FROM NEW.snapshot::text "
            "OR OLD.digest IS DISTINCT FROM NEW.digest OR OLD.created_by IS "
            "DISTINCT FROM NEW.created_by "
            "OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN RAISE "
            "EXCEPTION 'immutable draft'; "
            "END IF; RETURN NEW; END; $$"
        )
        op.execute(
            "CREATE TRIGGER linkedin_draft_guard BEFORE UPDATE OR DELETE ON linkedin_drafts "
            "FOR EACH ROW EXECUTE FUNCTION guard_linkedin_draft()"
        )
        op.execute(
            "CREATE TRIGGER linkedin_approval_guard BEFORE UPDATE OR DELETE ON linkedin_approvals "
            "FOR EACH ROW EXECUTE FUNCTION reject_touch_mutation()"
        )
    else:
        op.execute(
            "CREATE TRIGGER linkedin_draft_update BEFORE UPDATE ON linkedin_drafts "
            "WHEN OLD.task_id IS NOT NEW.task_id OR OLD.revision IS NOT NEW.revision "
            "OR OLD.snapshot IS NOT NEW.snapshot OR OLD.digest IS NOT NEW.digest "
            "OR OLD.created_by IS NOT NEW.created_by OR OLD.created_at IS NOT NEW.created_at "
            "BEGIN SELECT RAISE(ABORT, 'immutable draft'); END"
        )
        op.execute(
            "CREATE TRIGGER linkedin_draft_delete BEFORE DELETE ON linkedin_drafts "
            "BEGIN SELECT RAISE(ABORT, 'immutable draft'); END"
        )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER linkedin_approval_{action.lower()} BEFORE {action} "
                "ON linkedin_approvals "
                "BEGIN SELECT RAISE(ABORT, 'immutable approval'); END"
            )


def _drop_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER linkedin_approval_guard ON linkedin_approvals")
        op.execute("DROP TRIGGER linkedin_draft_guard ON linkedin_drafts")
        op.execute("DROP FUNCTION guard_linkedin_draft()")
    else:
        for name in (
            "linkedin_draft_update",
            "linkedin_draft_delete",
            "linkedin_approval_update",
            "linkedin_approval_delete",
        ):
            op.execute(f"DROP TRIGGER {name}")
