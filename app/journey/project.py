"""Read-only journey and next-action projections from immutable lead events."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.acquisition_models import LeadEvent, LeadScore
from app.db.models import User
from app.db.outreach_models import Mailbox
from app.db.research_models import Assessment
from app.db.session import Database

STAGES = [
    "Found",
    "Enriched",
    "Audited",
    "Drafted",
    "Approved",
    "Sent",
    "Follow-ups",
    "Replied",
    "Call",
    "Meeting",
    "Proposal",
    "Won",
    "Lost",
]
EVENT_STAGE = {
    "found": "Found",
    "enriched": "Enriched",
    "audited": "Audited",
    "drafted": "Drafted",
    "draft_edited": "Drafted",
    "approved": "Approved",
    "sent": "Sent",
    "followup_sent": "Follow-ups",
    "replied": "Replied",
    "call": "Call",
    "meeting": "Meeting",
}


def aware(value: str) -> datetime | None:
    """Parse stored schedule snapshots without accepting arbitrary executable values."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def derive(events: list[LeadEvent]) -> dict[str, Any]:
    """Calculate stages, stops, sender and pending work from the ordered event stream."""
    ordered = sorted(events, key=lambda item: (item.occurred_at, item.id))
    stage, stop, channel, mailbox = "Found", "", "", ""
    completed: set[str] = set()
    pending: dict[str, dict[str, Any]] = {}
    followups, followup_total = 0, 4
    next_action, due = "Review business evidence", None
    for item in ordered:
        data, kind = item.data, item.kind
        next_stage = EVENT_STAGE.get(kind)
        if kind == "stage_changed":
            proposed = str(data.get("stage", ""))
            next_stage = {
                "Call booked": "Call",
                "Call held": "Meeting",
                "Interested": "Replied",
                "Demo sent": "Replied",
                "Meeting booked": "Meeting",
                "Proposal sent": "Proposal",
            }.get(proposed, proposed)
            if next_stage not in STAGES:
                next_stage = None
            next_action = str(data.get("next_action", "Review the agreed next step"))
            due = aware(str(data.get("due_at", "")))
        if next_stage:
            completed.add(next_stage)
            if kind == "stage_changed" or STAGES.index(next_stage) >= STAGES.index(stage):
                stage = next_stage
        if data.get("followup_total") is not None:
            followup_total = int(data["followup_total"])
        if data.get("channel"):
            channel = str(data["channel"])
        if data.get("mailbox_id"):
            mailbox = str(data["mailbox_id"])
        if kind in {"stopped", "unsubscribed", "bounced"}:
            stop = str(data.get("reason") or kind.replace("_", " "))
            pending.clear()
            next_action, due = "No outreach: " + stop, None
        if kind == "scheduled":
            pending[str(data["intent_id"])] = data
        elif kind in {
            "sent",
            "followup_sent",
            "cancelled",
            "followup_cancelled",
            "held",
            "sending",
        }:
            pending.pop(str(data.get("intent_id", "")), None)
        if kind == "followup_sent":
            followups = max(followups, int(data.get("step", 0)))
        if kind == "replied":
            pending.clear()
            next_action, due = "Review the reply; automatic follow-ups cancelled", None
        if kind in {"action_needed", "enrichment_review", "held", "delivery_delay"}:
            next_action, due = str(data.get("reason") or "Review the delivery notice"), None
        if kind in {"drafted", "draft_edited"}:
            next_action, due = "Owner review required", None
        if kind == "approved":
            next_action, due = "Schedule the approved message", None
        if kind in {"call", "meeting"}:
            outcome = data.get("outcome", "")
            due = aware(str(data.get("due_at", "")))
            next_action = {
                "callback": "Call back",
                "meeting_booked": "Attend meeting",
                "interested": "Review the consented email draft",
                "no_answer": "Review before another call",
                "voicemail": "Review before another call",
            }.get(str(outcome), "Review call notes")
        if (
            kind == "scored"
            and data.get("band") == "Skip"
            and stage in {"Found", "Enriched", "Audited"}
        ):
            next_action = "Skip for now: score below the review threshold"
        elif kind == "scored" and next_action.startswith("Skip for now:"):
            next_action = "Review evidence and choose an outreach channel"
    if pending and not stop:
        scheduled = min(pending.values(), key=lambda value: str(value.get("due_at", "")))
        due = aware(str(scheduled.get("due_at", "")))
        next_action = (
            f"Follow-up {scheduled['step']} due"
            if scheduled.get("step")
            else "Approved first email due"
        )
    if stop:
        next_action, due = "No outreach: " + stop, None
    if stage in {"Won", "Lost"}:
        next_action, due = "Journey closed", None
    return {
        "stage": stage,
        "completed": completed,
        "stopped_reason": stop,
        "followups": followups,
        "followup_total": followup_total,
        "channel": channel,
        "mailbox_id": mailbox,
        "next_action": next_action,
        "due_at": due,
        "last": ordered[-1] if ordered else None,
        "stages": STAGES,
    }


def summary(session: Session, company_id: str) -> dict[str, Any]:
    """Attach readable actor/sender names and the latest score to the event projection."""
    events = list(
        session.scalars(
            select(LeadEvent)
            .where(LeadEvent.company_id == company_id)
            .order_by(LeadEvent.occurred_at, LeadEvent.id)
        )
    )
    result = derive(events)
    mailbox = session.get(Mailbox, result["mailbox_id"]) if result["mailbox_id"] else None
    result["mailbox"] = mailbox.email if mailbox else "Unassigned"
    result["score"] = session.scalar(
        select(LeadScore)
        .where(LeadScore.company_id == company_id)
        .order_by(LeadScore.created_at.desc(), LeadScore.id.desc())
        .limit(1)
    )
    assessment = session.scalar(
        select(Assessment)
        .where(Assessment.company_id == company_id)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    result["ai_score"] = assessment.result.get("score") if assessment else None
    due = result["due_at"]
    result["due_label"] = (
        due.astimezone(ZoneInfo("America/Edmonton")).strftime("%d %b, %I:%M %p Calgary")
        if due
        else ""
    )
    result["needs_today"] = (
        not result["stopped_reason"]
        and result["stage"] not in {"Won", "Lost"}
        and (
            due is None
            or due.astimezone(ZoneInfo("America/Edmonton")).date()
            <= utc_now().astimezone(ZoneInfo("America/Edmonton")).date()
        )
    )
    result["last_label"] = (
        result["last"]
        .occurred_at.astimezone(ZoneInfo("America/Edmonton"))
        .strftime("%d %b, %I:%M %p Calgary")
        if result["last"]
        else ""
    )
    return result


def detail(db: Database, company_id: str) -> dict[str, Any]:
    """Render chronological score history and a reverse-chronological linked timeline."""
    with db.sessions() as session:
        result = summary(session, company_id)
        events = list(
            session.scalars(
                select(LeadEvent)
                .where(LeadEvent.company_id == company_id)
                .order_by(LeadEvent.occurred_at.desc(), LeadEvent.id.desc())
            )
        )
        result["timeline"] = [
            {
                "event": item,
                "actor": actor.name
                if item.actor_id and (actor := session.get(User, item.actor_id))
                else "System / worker",
                "calgary": item.occurred_at.astimezone(ZoneInfo("America/Edmonton")).strftime(
                    "%d %b %Y, %I:%M %p"
                ),
                "pakistan": item.occurred_at.astimezone(ZoneInfo("Asia/Karachi")).strftime(
                    "%d %b %Y, %I:%M %p"
                ),
            }
            for item in events
        ]
        scores = list(
            session.scalars(
                select(LeadScore)
                .where(LeadScore.company_id == company_id)
                .order_by(LeadScore.created_at, LeadScore.id)
            )
        )
        result["history"] = scores
        result["chart_points"] = " ".join(
            f"{10 + (index * 480 / max(1, len(scores) - 1)):.1f},{110 - value.points * 10}"
            for index, value in enumerate(scores)
        )
        result["ai_assessment"] = session.scalar(
            select(Assessment)
            .where(Assessment.company_id == company_id)
            .order_by(Assessment.created_at.desc())
            .limit(1)
        )
        return result
