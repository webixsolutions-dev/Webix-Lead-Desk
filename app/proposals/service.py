"""Exact proposal revisions with owner-entered prices and attributable approval."""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation, Mailbox
from app.db.proposal_models import Proposal
from app.db.session import Database
from app.drafting.content import fingerprint
from app.drafting.context import email_context
from app.proposals.notes import latest_note, lock_booking
from app.proposals.schema import CURRENCIES as CURRENCIES
from app.proposals.schema import SECTIONS as SECTIONS


def amount(value: str) -> int | None:
    """Blank means unquoted; zero is an explicit owner price, with no currency conversion."""
    if not value.strip():
        return None
    try:
        number = Decimal(value)
        if (
            not number.is_finite()
            or number < 0
            or number > 1_000_000
            or number * 100 != (number * 100).to_integral_value()
        ):
            raise ValueError("Use an amount with at most two decimal places")
        return int(number * 100)
    except InvalidOperation as error:
        raise ValueError("Use a valid amount") from error


def identity(sender: Mailbox) -> dict[str, str]:
    """Bind the actual brand and sender identity without importing a different brand's logo."""
    return {
        "id": sender.id,
        "brand": sender.brand,
        "name": sender.sender_name,
        "email": sender.email,
        "address": sender.mailing_address,
        "contact": sender.contact_details,
    }


def conversation_digest(session: Session, booking: Booking, sender_id: str) -> str:
    """Invalidate review when an exact recipient's recorded conversation changes."""
    from app.db.lead_models import Contact

    thread = session.scalar(
        select(Conversation)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.company_id == booking.company_id,
            Conversation.mailbox_id == sender_id,
            Contact.email == booking.contact.strip().casefold(),
        )
    )
    if not thread:
        return ""
    if thread.state in {"stopped", "held"}:
        raise ValueError("Resolve the stopped or held conversation before preparing a proposal")
    return fingerprint(email_context(session, booking.company_id, thread.contact_id, sender_id))


def create(db: Database, actor_id: str, booking_id: str, fields: dict[str, str]) -> Proposal:
    """Prepare an immutable revision from confirmed notes, never generated commercial terms."""
    with db.sessions.begin() as session:
        booking = lock_booking(session, booking_id, actor_id)
        note = latest_note(session, booking_id)
        if (
            not note
            or note.id != fields.get("note_id")
            or note.booking_version != booking.version
            or booking.state != "completed"
        ):
            raise ValueError("Confirm current meeting notes before drafting a proposal")
        sender = session.get(Mailbox, fields.get("mailbox_id", ""))
        if not sender:
            raise ValueError("Choose the real sender identity")
        current = (
            session.scalar(
                select(func.max(Proposal.version)).where(Proposal.booking_id == booking_id)
            )
            or 0
        )
        if int(fields.get("version", "0")) != current:
            raise ValueError("Proposal changed in another tab; reload")
        sections = {key: fields.get(key, "").strip() for key, _ in SECTIONS}
        if any(not 10 <= len(value) <= 6000 for value in sections.values()):
            raise ValueError("Complete each proposal section with 10–6000 characters")
        title, currency = fields.get("title", "").strip(), fields.get("currency", "")
        period = fields.get("period", "monthly")
        if (
            not 5 <= len(title) <= 160
            or currency not in CURRENCIES
            or period not in {"monthly", "annual", "one_time"}
        ):
            raise ValueError("Choose a title, currency and billing period")
        setup, recurring = amount(fields.get("setup", "")), amount(fields.get("recurring", ""))
        data: dict[str, Any] = {
            "title": title,
            "booking_id": booking.id,
            "booking_version": booking.version,
            "company_id": booking.company_id,
            "recipient": booking.contact,
            "note_id": note.id,
            "notes_version": note.version,
            "sender": identity(sender),
            "sections": sections,
            "currency": currency,
            "setup_cents": setup,
            "recurring_cents": recurring,
            "period": period,
            "version": current + 1,
            "context_digest": conversation_digest(session, booking, sender.id),
        }
        from app.db.lead_models import Company

        company = session.get(Company, booking.company_id)
        data["company"] = company.name if company else ""
        for old in session.scalars(
            select(Proposal).where(
                Proposal.booking_id == booking_id, Proposal.state.in_(["draft", "approved"])
            )
        ):
            old.state = "superseded"
        proposal = Proposal(
            booking_id=booking_id,
            note_id=note.id,
            version=current + 1,
            snapshot=data,
            digest=fingerprint(data),
            created_by=actor_id,
        )
        session.add(proposal)
        session.flush()
        session.add(AuditEvent(actor_id=actor_id, action="proposal.created", entity_id=proposal.id))
        return proposal


def require_current(session: Session, proposal: Proposal, booking: Booking) -> None:
    """Reject changed notes, identity, recipient or conversations before approval and export."""
    note = latest_note(session, booking.id)
    sender = session.get(Mailbox, proposal.snapshot["sender"]["id"])
    current = session.scalar(
        select(func.max(Proposal.version)).where(Proposal.booking_id == booking.id)
    )
    if (
        current != proposal.version
        or not note
        or note.id != proposal.note_id
        or booking.state != "completed"
        or booking.version != proposal.snapshot["booking_version"]
        or not sender
        or identity(sender) != proposal.snapshot["sender"]
        or fingerprint(proposal.snapshot) != proposal.digest
        or conversation_digest(session, booking, sender.id) != proposal.snapshot["context_digest"]
    ):
        raise ValueError("Proposal context changed; save and review a new revision")


def approve(db: Database, actor_id: str, proposal_id: str, digest: str) -> None:
    """Approve the exact document, recipient and prices without sending or scheduling it."""
    with db.sessions.begin() as session:
        item = session.get(Proposal, proposal_id)
        if not item:
            raise ValueError("Proposal not found")
        booking = lock_booking(session, item.booking_id, actor_id)
        session.refresh(item)
        require_current(session, item, booking)
        if item.state != "draft" or item.digest != digest:
            raise ValueError("Proposal changed or already reviewed; reload")
        if item.snapshot["setup_cents"] is None and item.snapshot["recurring_cents"] is None:
            raise ValueError("Enter the agreed pricing before approval; blank does not mean free")
        item.state, item.approved_by, item.approved_at = "approved", actor_id, utc_now()
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="proposal.approved",
                entity_id=item.id,
                details={"digest": digest},
            )
        )
