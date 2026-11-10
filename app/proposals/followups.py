"""Keep review reminders separate from permission to continue a conversation."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage
from app.db.lead_models import Contact
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity
from app.db.proposal_models import Proposal


def hold_reason(session: Session, proposal: Proposal) -> str:
    """Hold old sequences on new replies, stops, closed deals or a replacement proposal."""
    latest = session.scalar(
        select(func.max(Proposal.version)).where(Proposal.booking_id == proposal.booking_id)
    )
    booking = session.get(Booking, proposal.booking_id)
    if latest != proposal.version or not booking or booking.state != "completed":
        return "Proposal or booking changed; review the current record"
    thread = session.scalar(
        select(Conversation)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.company_id == booking.company_id,
            Conversation.mailbox_id == proposal.snapshot["sender"]["id"],
            Contact.email == proposal.snapshot["recipient"].strip().casefold(),
        )
    )
    if not thread:
        return "Check the actual conversation manually before following up"
    card = session.scalar(select(Opportunity).where(Opportunity.conversation_id == thread.id))
    if thread.state in {"stopped", "held"} or (card and card.stage in {"Won", "Lost"}):
        return "Outreach is stopped, held or closed; do not continue this sequence"
    if proposal.sent_at and session.scalar(
        select(InboxMessage.id)
        .where(
            InboxMessage.conversation_id == thread.id,
            InboxMessage.mailbox_id == thread.mailbox_id,
            InboxMessage.sender == proposal.snapshot["recipient"].strip().casefold(),
            InboxMessage.received_at > proposal.sent_at,
            InboxMessage.classification.not_in(["automatic", "delivery_delayed"]),
        )
        .limit(1)
    ):
        return "A new reply arrived; review and answer it instead of following this sequence"
    return ""
