"""linkedin identity and coordination"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0015_linkedin_identity"
down_revision: str | None = "0014_journey_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "linkedin_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("profile_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("voice_card", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_url"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "company_coordination",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("hold_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_table(
        "people",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=160), nullable=False),
        sa.Column("decision_maker", sa.Boolean(), nullable=False),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("personal_email", sa.String(length=254), nullable=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("presence", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=False),
        sa.Column("verification_note", sa.Text(), nullable=False),
        sa.Column("verified_by", sa.String(length=36), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contact_id"),
        sa.UniqueConstraint("linkedin_url"),
        sa.UniqueConstraint("personal_email"),
    )
    op.create_index(op.f("ix_people_company_id"), "people", ["company_id"], unique=False)
    op.create_table(
        "person_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("search_id", sa.String(length=36), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["search_id"],
            ["search_records.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_person_candidates_company_id"), "person_candidates", ["company_id"], unique=False
    )
    op.create_table(
        "linkedin_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        op.f("ix_linkedin_tasks_company_id"), "linkedin_tasks", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_linkedin_tasks_person_id"), "linkedin_tasks", ["person_id"], unique=False
    )
    op.create_index(
        op.f("ix_linkedin_tasks_profile_id"), "linkedin_tasks", ["profile_id"], unique=False
    )
    op.create_index(op.f("ix_linkedin_tasks_state"), "linkedin_tasks", ["state"], unique=False)
    op.create_table(
        "person_identities",
        sa.Column("key", sa.String(length=600), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        op.f("ix_person_identities_person_id"), "person_identities", ["person_id"], unique=False
    )
    op.create_table(
        "person_ownership",
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("warm", sa.Boolean(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.create_table(
        "touches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=True),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["linkedin_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index(op.f("ix_touches_company_id"), "touches", ["company_id"], unique=False)
    op.create_index(op.f("ix_touches_occurred_at"), "touches", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_touches_person_id"), "touches", ["person_id"], unique=False)

    bind = op.get_bind()
    touches = sa.Table("touches", sa.MetaData(), autoload_with=bind)
    coordination = sa.Table("company_coordination", sa.MetaData(), autoload_with=bind)
    seen: set[str] = set()
    query = (
        "SELECT * FROM lead_events WHERE kind IN "
        "('sent','followup_sent','replied','call','stopped') ORDER BY occurred_at"
    )
    for row in bind.execute(sa.text(query)).mappings():
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        at = (
            datetime.fromisoformat(row["occurred_at"])
            if isinstance(row["occurred_at"], str)
            else row["occurred_at"]
        )
        inbound = row["kind"] in {"replied", "stopped"}
        bind.execute(
            touches.insert().values(
                id=str(uuid4()),
                event_key="historical:" + row["id"],
                company_id=row["company_id"],
                channel=data.get("channel") or ("phone" if row["kind"] == "call" else "email"),
                direction="inbound" if inbound else "outbound",
                outcome=data.get("classification", row["kind"]),
                text="",
                actor_id=row["actor_id"],
                occurred_at=at,
            )
        )
        if inbound and row["company_id"] not in seen:
            bind.execute(
                coordination.insert().values(
                    company_id=row["company_id"],
                    revision=0,
                    state="replied",
                    reason="Historical reply or stop requires owner review",
                    updated_at=datetime.now(UTC),
                )
            )
            seen.add(row["company_id"])
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_touch_mutation() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'touches is append-only'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER touches_immutable BEFORE UPDATE OR DELETE ON touches "
            "FOR EACH ROW EXECUTE FUNCTION reject_touch_mutation()"
        )
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER touches_no_{action.lower()} BEFORE {action} ON touches "
                "BEGIN SELECT RAISE(ABORT, 'touches is append-only'); END"
            )


def downgrade() -> None:
    """Revert the migration after explicitly removing its immutable journal guards."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER touches_immutable ON touches")
        op.execute("DROP FUNCTION reject_touch_mutation()")
    else:
        op.execute("DROP TRIGGER touches_no_update")
        op.execute("DROP TRIGGER touches_no_delete")
    op.drop_index(op.f("ix_touches_person_id"), table_name="touches")
    op.drop_index(op.f("ix_touches_occurred_at"), table_name="touches")
    op.drop_index(op.f("ix_touches_company_id"), table_name="touches")
    op.drop_table("touches")
    op.drop_table("person_ownership")
    op.drop_index(op.f("ix_person_identities_person_id"), table_name="person_identities")
    op.drop_table("person_identities")
    op.drop_index(op.f("ix_linkedin_tasks_state"), table_name="linkedin_tasks")
    op.drop_index(op.f("ix_linkedin_tasks_profile_id"), table_name="linkedin_tasks")
    op.drop_index(op.f("ix_linkedin_tasks_person_id"), table_name="linkedin_tasks")
    op.drop_index(op.f("ix_linkedin_tasks_company_id"), table_name="linkedin_tasks")
    op.drop_table("linkedin_tasks")
    op.drop_index(op.f("ix_person_candidates_company_id"), table_name="person_candidates")
    op.drop_table("person_candidates")
    op.drop_index(op.f("ix_people_company_id"), table_name="people")
    op.drop_table("people")
    op.drop_table("company_coordination")
    op.drop_table("linkedin_profiles")
