"""Hosted backup receipts and default-deny RLS on every application table."""

import sqlalchemy as sa
from alembic import op

revision = "0020_hosted_supabase"
down_revision = "0019_linkedin_metrics"
branch_labels = None
depends_on = None

TABLES = (
    "ai_budget_accounts",
    "ai_calls",
    "alembic_version",
    "assessments",
    "audit_events",
    "backup_status",
    "call_notes",
    "call_records",
    "campaign_leads",
    "campaign_runs",
    "campaigns",
    "channel_reservations",
    "companies",
    "company_coordination",
    "company_domains",
    "contact_bases",
    "contacts",
    "conversations",
    "demo_versions",
    "draft_approvals",
    "drafts",
    "evidence",
    "form_drafts",
    "inbox_cursors",
    "inbox_messages",
    "integration_secrets",
    "job_attempts",
    "jobs",
    "lead_events",
    "lead_scores",
    "learning_proposals",
    "linkedin_approvals",
    "linkedin_drafts",
    "linkedin_oauth_states",
    "linkedin_policies",
    "linkedin_post_approvals",
    "linkedin_post_media",
    "linkedin_post_media_chunks",
    "linkedin_post_observations",
    "linkedin_post_revisions",
    "linkedin_posting_connections",
    "linkedin_posts",
    "linkedin_profile_observations",
    "linkedin_profile_risk_reviews",
    "linkedin_profiles",
    "linkedin_quotas",
    "linkedin_sequences",
    "linkedin_tasks",
    "local_team_members",
    "login_throttles",
    "mailbox_days",
    "mailbox_states",
    "mailboxes",
    "offers",
    "opportunities",
    "partner_days",
    "partner_reviews",
    "partner_slots",
    "people",
    "person_candidates",
    "person_days",
    "person_identities",
    "person_ownership",
    "person_pipeline",
    "pilot_members",
    "pilot_reservations",
    "pilots",
    "pipeline_events",
    "playbook_versions",
    "proof_cards",
    "runtime_control",
    "score_policies",
    "search_buckets",
    "search_records",
    "send_attempts",
    "send_intents",
    "sessions",
    "settings",
    "source_records",
    "suppression_entries",
    "touches",
    "users",
    "website_audits",
    "worker_instances",
)


def upgrade() -> None:
    """Keep SQLite portable; protect only application tables in PostgreSQL public."""
    op.create_table(
        "backup_status",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("namespace", sa.String(36), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("claim", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_bytes", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        for name in TABLES:
            # No policies are created: ordinary Data API roles cannot see any rows.
            op.execute(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Remove the receipt table; retain RLS on existing tables as defense in depth."""
    op.drop_table("backup_status")
