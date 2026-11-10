"""Record actual manual proposal delivery and reviewed follow-up reminders."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity, PipelineEvent
from app.db.proposal_models import Proposal, ProposalFollowup
from app.db.session import Database
from app.proposals.notes import lock_booking
from app.proposals.service import require_current


def record_sent(
    db: Database, actor_id: str, proposal_id: str, digest: str, sent_at: datetime, reference: str
) -> None:
    """Record the owner's actual manual send, not a download, then create review reminders."""
    if sent_at.tzinfo is None or sent_at > utc_now() or not 10 <= len(reference.strip()) <= 500:
        raise ValueError(
            "Record the actual past send time and a 10–500 character delivery reference"
        )
    with db.sessions.begin() as session:
        item = session.get(Proposal, proposal_id)
        if not item:
            raise ValueError("Proposal not found")
        booking = lock_booking(session, item.booking_id, actor_id)
        session.refresh(item)
        require_current(session, item, booking)
        if (
            item.state != "approved"
            or item.digest != digest
            or not item.approved_at
            or sent_at < item.approved_at
        ):
            raise ValueError(
                "Only this exact approved revision can be recorded as sent after approval"
            )
        item.state, item.sent_at, item.sent_reference = "sent", sent_at, reference.strip()
        for day in (2, 5, 9):
            session.add(
                ProposalFollowup(proposal_id=item.id, day=day, due_at=sent_at + timedelta(days=day))
            )
        # Match the actual recipient, not just a company that might have other contacts.
        from app.db.lead_models import Contact

        card = session.scalar(
            select(Opportunity)
            .join(Conversation)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(
                Conversation.company_id == booking.company_id,
                Conversation.mailbox_id == item.snapshot["sender"]["id"],
                Contact.email == booking.contact.strip().casefold(),
            )
        )
        if card and card.stage in {
            "Call held",
            "Meeting held",
            "Proposal",
            "Proposal sent",
            "Negotiation",
        }:
            card.stage, card.version = "Proposal sent", card.version + 1
            card.next_action, card.next_due_at = (
                "Review proposal follow-up",
                sent_at + timedelta(days=2),
            )
            card.updated_at, card.updated_by = utc_now(), actor_id
            session.add(
                PipelineEvent(
                    opportunity_id=card.id,
                    stage="Proposal sent",
                    note="Owner recorded delivery of approved proposal revision.",
                    actor_id=actor_id,
                )
            )
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="proposal.sent_recorded",
                entity_id=item.id,
                details={"digest": digest},
            )
        )


def review_followup(db: Database, actor_id: str, followup_id: str, state: str, note: str) -> None:
    """Resolve a reminder with a human reason; this action never queues an outgoing message."""
    if state not in {"reviewed", "skipped"} or not 10 <= len(note.strip()) <= 1000:
        raise ValueError("Choose reviewed or skipped and record a specific reason")
    with db.sessions.begin() as session:
        task = session.get(ProposalFollowup, followup_id)
        proposal = session.get(Proposal, task.proposal_id) if task else None
        if not task or not proposal:
            raise ValueError("Follow-up not found")
        lock_booking(session, proposal.booking_id, actor_id)
        session.refresh(task)
        if task.state != "pending":
            raise ValueError("Follow-up already reviewed; reload")
        task.state, task.note, task.reviewed_by = state, note.strip(), actor_id
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="proposal.followup_reviewed",
                entity_id=task.id,
                details={"state": state},
            )
        )
