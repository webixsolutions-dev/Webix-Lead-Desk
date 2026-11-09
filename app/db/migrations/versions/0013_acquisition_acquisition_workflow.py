"""acquisition workflow"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_acquisition"
down_revision: str | None = "0012_outreach_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""

    op.create_table(
        "search_buckets",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("committed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "lead_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("item_url", sa.String(length=2000), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index(op.f("ix_lead_events_company_id"), "lead_events", ["company_id"], unique=False)
    op.create_index(
        op.f("ix_lead_events_occurred_at"), "lead_events", ["occurred_at"], unique=False
    )
    op.create_table(
        "lead_scores",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(length=24), nullable=False),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "campaign_id", "fingerprint"),
    )
    op.create_index(
        op.f("ix_lead_scores_campaign_id"), "lead_scores", ["campaign_id"], unique=False
    )
    op.create_index(op.f("ix_lead_scores_company_id"), "lead_scores", ["company_id"], unique=False)
    op.create_table(
        "score_policies",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "search_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("cache_key", sa.String(length=100), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("ai_call_id", sa.String(length=36), nullable=True),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ai_call_id"],
            ["ai_calls.id"],
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_key"),
    )
    op.create_index(
        op.f("ix_search_records_cache_key"), "search_records", ["cache_key"], unique=False
    )
    op.create_index(
        op.f("ix_search_records_company_id"), "search_records", ["company_id"], unique=False
    )
    op.create_table(
        "call_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("consent_note", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_key"),
    )
    op.create_index(
        op.f("ix_call_records_company_id"), "call_records", ["company_id"], unique=False
    )
    op.create_table(
        "call_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("call_id", sa.String(length=36), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["call_records.id"],
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["drafts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id"),
    )
    op.add_column(
        "companies",
        sa.Column("has_website", sa.String(length=8), server_default="unknown", nullable=False),
    )


def downgrade() -> None:
    """Revert the migration."""

    op.drop_column("companies", "has_website")
    op.drop_table("call_notes")
    op.drop_index(op.f("ix_call_records_company_id"), table_name="call_records")
    op.drop_table("call_records")
    op.drop_index(op.f("ix_search_records_company_id"), table_name="search_records")
    op.drop_index(op.f("ix_search_records_cache_key"), table_name="search_records")
    op.drop_table("search_records")
    op.drop_table("score_policies")
    op.drop_index(op.f("ix_lead_scores_company_id"), table_name="lead_scores")
    op.drop_index(op.f("ix_lead_scores_campaign_id"), table_name="lead_scores")
    op.drop_table("lead_scores")
    op.drop_index(op.f("ix_lead_events_occurred_at"), table_name="lead_events")
    op.drop_index(op.f("ix_lead_events_company_id"), table_name="lead_events")
    op.drop_table("lead_events")
    op.drop_table("search_buckets")
