"""Backfill historical journey facts and enforce append-only journals."""

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0014_journey_backfill"
down_revision: str | None = "0013_acquisition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve historical source, audit, approval, send, reply and pipeline decisions."""
    bind = op.get_bind()
    table = sa.Table("lead_events", sa.MetaData(), autoload_with=bind)

    def append(
        company: str,
        key: str,
        kind: str,
        at: Any,
        data: dict[str, Any],
        actor: str | None = None,
        url: str = "",
    ) -> None:
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if not isinstance(at, datetime):
            at = datetime.now(UTC)
        bind.execute(
            table.insert().values(
                id=str(uuid4()),
                company_id=company,
                event_key=key,
                kind=kind,
                actor_id=actor,
                item_url=url or f"/leads/{company}",
                data=data,
                occurred_at=at,
            )
        )

    for r in bind.execute(sa.text("SELECT id, created_at FROM companies")).mappings():
        append(
            r["id"], "company:" + r["id"], "found", r["created_at"], {"source": "historical import"}
        )
    for r in bind.execute(sa.text("SELECT * FROM source_records")).mappings():
        append(
            r["company_id"],
            "source:" + r["id"],
            "source_found",
            r["observed_at"],
            {"source": r["source"], "source_url": r["source_url"]},
        )
    for r in bind.execute(sa.text("SELECT * FROM website_audits")).mappings():
        append(
            r["company_id"],
            "audit:" + r["id"],
            "audited",
            r["created_at"],
            {"audit_id": r["id"], "summary": r["summary"], "state": r["state"]},
        )
    for r in bind.execute(sa.text("SELECT * FROM assessments")).mappings():
        result = json.loads(r["result"]) if isinstance(r["result"], str) else r["result"]
        append(
            r["company_id"],
            "assessment:" + r["id"],
            "ai_assessed",
            r["created_at"],
            {"assessment_id": r["id"], "score": result.get("score"), "origin": r["origin"]},
        )
    for r in bind.execute(sa.text("SELECT * FROM demo_versions")).mappings():
        append(
            r["company_id"],
            f"demo:{r['id']}:{r['state']}",
            "concept_" + r["state"],
            r["approved_at"] or r["created_at"],
            {"demo_id": r["id"], "version": r["version"]},
            r["approved_by"] or r["created_by"],
            "/demos",
        )
    for r in bind.execute(
        sa.text(
            "SELECT d.*, c.company_id FROM drafts d JOIN conversations c ON c.id=d.conversation_id"
        )
    ).mappings():
        append(
            r["company_id"],
            "draft:" + r["id"],
            "draft_edited" if r["revision"] > 1 else "drafted",
            r["created_at"],
            {"draft_id": r["id"], "revision": r["revision"], "step": r["sequence_step"]},
            r["created_by"],
            "/drafts/" + r["id"],
        )
    for r in bind.execute(
        sa.text(
            "SELECT a.*, c.company_id FROM draft_approvals a JOIN drafts d ON "
            "d.id=a.draft_id JOIN conversations c ON c.id=d.conversation_id"
        )
    ).mappings():
        append(
            r["company_id"],
            "approval:" + r["draft_id"],
            "approved",
            r["approved_at"],
            {"draft_id": r["draft_id"]},
            r["approved_by"],
            "/drafts/" + r["draft_id"],
        )
    for r in bind.execute(
        sa.text(
            "SELECT s.*, c.company_id, d.kind, d.sequence_step FROM send_intents s JOIN "
            "conversations c ON c.id=s.conversation_id JOIN drafts d ON d.id=s.draft_id"
        )
    ).mappings():
        kind = {
            "accepted": "followup_sent" if r["kind"] == "follow_up" else "sent",
            "cancelled": "followup_cancelled" if r["sequence_step"] else "cancelled",
            "uncertain": "held",
            "held": "held",
            "scheduled": "scheduled",
        }.get(r["state"], "sending")
        append(
            r["company_id"],
            "historical_intent:" + r["id"] + ":" + r["state"],
            kind,
            r["accepted_at"] or r["attempted_at"] or r["due_at"],
            {
                "intent_id": r["id"],
                "message_id": r["message_id"],
                "mailbox_id": r["mailbox_id"],
                "step": r["sequence_step"],
                "due_at": str(r["due_at"]),
                "reason": r["hold_reason"],
                "channel": "email",
            },
            url="/drafts/" + r["draft_id"],
        )
    for r in bind.execute(
        sa.text(
            "SELECT i.*, c.company_id FROM inbox_messages i JOIN conversations c ON "
            "c.id=i.conversation_id"
        )
    ).mappings():
        kind = {
            "bounce": "bounced",
            "unsubscribe": "unsubscribed",
            "delivery_delayed": "delivery_delay",
            "delivery_notice": "delivery_notice",
            "not_interested": "stopped",
        }.get(r["classification"], "replied")
        append(
            r["company_id"],
            "inbox:" + r["id"],
            kind,
            r["received_at"],
            {
                "classification": r["classification"],
                "reason": r["classification"],
                "mailbox_id": r["mailbox_id"],
            },
            url="/inbox?conversation=" + r["conversation_id"],
        )
    for r in bind.execute(
        sa.text(
            "SELECT p.*, c.company_id FROM pipeline_events p JOIN opportunities o ON "
            "o.id=p.opportunity_id JOIN conversations c ON c.id=o.conversation_id"
        )
    ).mappings():
        append(
            r["company_id"],
            "pipeline:" + r["id"],
            "stage_changed",
            r["created_at"],
            {"stage": r["stage"], "reason": r["note"]},
            r["actor_id"],
            "/pipeline",
        )
    for r in bind.execute(
        sa.text(
            "SELECT * FROM audit_events WHERE action IN ('company.website_review', "
            "'conversation.stopped', 'draft.rejected')"
        )
    ).mappings():
        company = r["entity_id"] if r["action"] == "company.website_review" else None
        if r["action"] == "conversation.stopped":
            company = bind.scalar(
                sa.text("SELECT company_id FROM conversations WHERE id=:id"), {"id": r["entity_id"]}
            )
        if r["action"] == "draft.rejected":
            company = bind.scalar(
                sa.text(
                    "SELECT c.company_id FROM conversations c JOIN drafts d ON "
                    "d.conversation_id=c.id WHERE d.id=:id"
                ),
                {"id": r["entity_id"]},
            )
        if company:
            details = json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]
            append(
                company,
                "legacy_audit:" + r["id"],
                {
                    "company.website_review": "enriched",
                    "conversation.stopped": "stopped",
                    "draft.rejected": "draft_rejected",
                }[r["action"]],
                r["created_at"],
                {"audit_event_id": r["id"], "reason": details.get("reason", r["action"])},
                r["actor_id"],
            )
    # Existing free-text observations remain unconfirmed structured signals. Seed
    # only targeting points so old leads have an honest score and history entry.
    score_table = sa.Table("lead_scores", sa.MetaData(), autoload_with=bind)
    labels = {
        "industry": "Target industry",
        "active_ads": "Active ads confirmed",
        "weak_mobile": "Weak or broken mobile website",
        "no_website": "No website confirmed",
        "response_gap": "Quote response or after-hours booking gap confirmed",
        "reviews": "At least ten Google reviews confirmed",
        "mature": "Mature tooling or strong modern website",
    }
    query = (
        "SELECT c.id, c.industry, m.campaign_id, p.industry AS target "
        "FROM companies c JOIN campaign_leads m ON m.company_id=c.id "
        "JOIN campaigns p ON p.id=m.campaign_id"
    )
    for r in bind.execute(sa.text(query)).mappings():
        targets = [
            part.strip().casefold() for part in re.split(r"[,;&]", r["target"]) if part.strip()
        ]
        matched = any(
            re.search(r"\b" + re.escape(part) + r"\b", r["industry"].casefold()) for part in targets
        )
        points, score_id, now = 2 if matched else 0, str(uuid4()), datetime.now(UTC)
        reasons = [
            {
                "signal": signal,
                "label": label,
                "points": points if signal == "industry" else 0,
                "state": "confirmed" if signal == "industry" and matched else "unknown",
                "source_url": "/leads?campaign=" + r["campaign_id"] if signal == "industry" else "",
                "evidence_id": "",
                "observed_at": "",
            }
            for signal, label in labels.items()
        ]
        reason = "Initial score from campaign targeting; observations await structured review"
        bind.execute(
            score_table.insert().values(
                id=score_id,
                company_id=r["id"],
                campaign_id=r["campaign_id"],
                policy_version=0,
                points=points,
                band="Skip",
                breakdown=reasons,
                reason=reason,
                fingerprint=hashlib.sha256(score_id.encode()).hexdigest(),
                created_at=now,
            )
        )
        append(
            r["id"],
            "score:" + score_id,
            "scored",
            now,
            {
                "points": points,
                "band": "Skip",
                "reason": reason,
                "score_id": score_id,
                "campaign_id": r["campaign_id"],
            },
        )
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_lead_event_mutation() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'lead_events is append-only'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER lead_events_immutable BEFORE UPDATE OR DELETE ON lead_events "
            "FOR EACH ROW EXECUTE FUNCTION reject_lead_event_mutation()"
        )
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER lead_events_no_{action.lower()} BEFORE {action} ON "
                f"lead_events BEGIN SELECT RAISE(ABORT, 'lead_events is append-only'); END"
            )


def downgrade() -> None:
    """Remove protections only during an explicit schema rollback."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER lead_events_immutable ON lead_events")
        op.execute("DROP FUNCTION reject_lead_event_mutation()")
    else:
        op.execute("DROP TRIGGER lead_events_no_update")
        op.execute("DROP TRIGGER lead_events_no_delete")
    op.execute("DELETE FROM lead_events")
