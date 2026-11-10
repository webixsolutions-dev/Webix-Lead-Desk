"""Advance only the exact email contact's card after an owner confirms a held meeting."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.lead_models import Contact
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity, PipelineEvent


def meeting_held(session: Session, booking: Booking, actor_id: str) -> None:
    """A completed real meeting can advance a card; a proposed booking cannot."""
    card = session.scalar(
        select(Opportunity)
        .join(Conversation)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.company_id == booking.company_id,
            Contact.email == booking.contact.strip().casefold(),
            Conversation.state.not_in(["stopped", "held"]),
        )
    )
    if not card or card.stage in {"Proposal sent", "Negotiation", "Won", "Lost"}:
        return
    card.stage, card.version = "Meeting held", card.version + 1
    card.meeting_at, card.updated_by, card.updated_at = booking.starts_at, actor_id, utc_now()
    card.next_action, card.next_due_at = "Confirm meeting notes and prepare proposal", utc_now()
    session.add(
        PipelineEvent(
            opportunity_id=card.id,
            stage="Meeting held",
            note="Owner marked the confirmed past booking completed.",
            actor_id=actor_id,
        )
    )
