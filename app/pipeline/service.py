"""Human-reviewed sales stages and internal call cards; never outbound automation."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.lead_models import Company, Contact
from app.db.models import User
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity, PipelineEvent
from app.db.session import Database
from app.learning.capture import capture
from app.pipeline.handoff import call_due
from app.sending.window import CALGARY
from app.sources.validation import website_url

STAGES = (
    "Replied",
    "Interested",
    "Demo sent",
    "Call booked",
    "Call held",
    "Proposal",
    "Won",
    "Lost",
)
QUALIFIED = {"Interested", "Demo sent", "Call booked", "Call held", "Proposal"}
TRANSITIONS = {
    "Replied": {"Interested", "Call booked", "Lost"},
    "Interested": {"Call booked", "Lost"},
    "Demo sent": {"Call booked", "Lost"},
    "Call booked": {"Call held", "Lost"},
    "Call held": {"Proposal", "Lost"},
    "Proposal": {"Won", "Lost"},
    "Won": set(),
    "Lost": set(),
}


def capture_reply(session: Session, conversation: Conversation, label: str, now: datetime) -> None:
    """Create a review card for an inbound reply without trusting it to advance the sales stage."""
    card = session.scalar(select(Opportunity).where(Opportunity.conversation_id == conversation.id))
    if label in {"bounce", "unsubscribe", "not_interested", "automatic"} and card is None:
        return
    if card is None:
        card = Opportunity(
            conversation_id=conversation.id,
            last_reply_at=now,
            next_due_at=call_due(now) if label == "interested" else None,
        )
        session.add(card)
        session.flush()
        session.add(
            PipelineEvent(
                opportunity_id=card.id,
                stage="Replied",
                note="Incoming reply needs human review. No message or sales stage was approved.",
            )
        )
    else:
        card.last_reply_at = now
        if label == "interested" and card.stage not in {"Won", "Lost"}:
            card.next_due_at = call_due(now)


def update_opportunity(
    db: Database,
    card_id: str,
    expected_version: int,
    actor_id: str,
    *,
    stage: str,
    assigned_to: str | None,
    summary: str,
    next_action: str,
    meeting_at: datetime | None,
    meeting_url: str,
    setup_cad_cents: int,
    recurring_cad_cents: int,
    outcome_note: str,
) -> None:
    """Apply an attributable team decision with stage validation and optimistic conflict checks."""
    if stage not in STAGES or not 10 <= len(summary.strip()) <= 3000 or len(next_action) > 500:
        raise ValueError("Choose a sales stage and write a specific summary/next step")
    if not 0 <= setup_cad_cents <= 100_000_000 or not 0 <= recurring_cad_cents <= 100_000_000:
        raise ValueError("Use valid non-negative CAD amounts")
    if len(outcome_note) > 2000 or (stage in {"Won", "Lost"} and len(outcome_note.strip()) < 10):
        raise ValueError("Record an outcome reason for won or lost opportunities")
    link = website_url(meeting_url) if meeting_url else ""
    if stage == "Call held" and (not meeting_at or meeting_at > utc_now()):
        raise ValueError("Record the actual past call time before marking it held")
    if stage == "Call booked" and (not meeting_at or not assigned_to):
        raise ValueError("A booked call needs a time and assigned team member")
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        card = session.get(Opportunity, card_id)
        assignee = session.get(User, assigned_to) if assigned_to else None
        if not actor or not actor.active or not card:
            raise ValueError("Choose an existing opportunity and active team member")
        if assigned_to and (not assignee or not assignee.active):
            raise ValueError("Choose an active team member")
        if stage != card.stage and stage not in TRANSITIONS.get(card.stage, set()):
            raise ValueError(
                "Choose an available next stage; demo delivery is recorded after acceptance"
            )
        conversation = session.get(Conversation, card.conversation_id)
        if stage in QUALIFIED and conversation and conversation.state == "stopped":
            raise ValueError("This recipient has stopped outreach; preserve that stop")
        if stage in QUALIFIED and conversation and conversation.state == "held":
            if session.scalar(
                select(SendIntent.id)
                .where(
                    SendIntent.conversation_id == conversation.id, SendIntent.state == "uncertain"
                )
                .limit(1)
            ):
                raise ValueError("Resolve uncertain delivery before qualifying this conversation")
            conversation.state = "replied"
        due = card.next_due_at
        if stage == "Interested" and card.stage == "Replied":
            due = call_due(utc_now())
        if stage == "Call booked":
            due = meeting_at
        changed = session.scalar(
            update(Opportunity)
            .where(Opportunity.id == card_id, Opportunity.version == expected_version)
            .values(
                stage=stage,
                version=Opportunity.version + 1,
                assigned_to=assigned_to,
                summary=summary.strip(),
                next_action=next_action.strip(),
                next_due_at=due,
                meeting_at=meeting_at,
                meeting_url=link,
                setup_cad_cents=setup_cad_cents,
                recurring_cad_cents=recurring_cad_cents,
                outcome_note=outcome_note.strip(),
                updated_by=actor_id,
                updated_at=utc_now(),
            )
            .returning(Opportunity.id)
        )
        if not changed:
            raise ValueError("Opportunity changed in another tab; reload before saving")
        if stage in {"Won", "Lost"} and conversation:
            capture(
                session,
                "outcome",
                card_id + ":" + str(expected_version + 1),
                conversation.campaign_id,
                actor_id,
                "Review a recorded sales outcome",
                "Consider this reviewed outcome as one observation, not proof of causation: "
                + outcome_note.strip(),
                {"opportunity_id": card_id, "stage": stage, "reason": outcome_note.strip()},
            )
        session.add(
            PipelineEvent(
                opportunity_id=card_id,
                stage=stage,
                note=outcome_note.strip() or next_action.strip() or "Updated opportunity review",
                actor_id=actor_id,
            )
        )


def board(db: Database) -> dict[str, Any]:
    """Read real opportunities, their business/contact context and active assignment choices."""
    with db.sessions() as session:
        rows = list(
            session.execute(
                select(Opportunity, Conversation, Company, Contact)
                .join(Conversation, Conversation.id == Opportunity.conversation_id)
                .join(Company, Company.id == Conversation.company_id)
                .join(Contact, Contact.id == Conversation.contact_id)
                .order_by(Opportunity.updated_at.desc())
                .limit(300)
            )
        )
        return {
            "opportunities": rows,
            "calgary_zone": CALGARY,
            "pipeline_stages": STAGES,
            "team_members": list(session.scalars(select(User).where(User.active.is_(True)))),
        }
