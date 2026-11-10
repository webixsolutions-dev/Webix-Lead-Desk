"""Synthetic meeting and proposal for browser and Word export verification."""

from datetime import timedelta

from sqlalchemy import select

from app.bookings.service import decide, save
from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.lead_models import Company
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.proposals.notes import confirm_notes
from app.proposals.service import create


def seed(db: Database, actor: str) -> dict[str, str]:
    """Create fictional confirmed scope and prices without external meeting or mail calls."""
    with db.sessions() as session:
        company = session.scalar(select(Company))
        mailbox = session.scalar(select(Mailbox))
        assert company and mailbox
    start = utc_now() + timedelta(days=2)
    booking = save(
        db,
        actor,
        {
            "company_id": company.id,
            "title": "Website scope discussion",
            "contact": "service@example.com",
            "host": "Demo Owner",
            "starts_at": start.replace(tzinfo=None).isoformat(),
            "timezone": "UTC",
            "minutes": "30",
            "mode": "video",
            "location": "https://example.org/meeting",
            "note": "Fictional appointment for interface verification.",
        },
    )
    decide(db, actor, booking.id, 1, "confirmed")
    with db.sessions.begin() as session:
        item = session.get(Booking, booking.id)
        assert item
        item.starts_at = utc_now() - timedelta(hours=2)
        item.ends_at = utc_now() - timedelta(hours=1)
    decide(db, actor, booking.id, 2, "completed")
    note = confirm_notes(
        db,
        actor,
        booking.id,
        3,
        0,
        "We discussed five service pages, approved brand assets and a clearer request form.",
    )
    p = create(
        db,
        actor,
        booking.id,
        {
            "note_id": note.id,
            "mailbox_id": mailbox.id,
            "version": "0",
            "title": "Service website proposal",
            "summary": note.text,
            "scope": "Five service pages, mobile layouts and a request form using supplied assets.",
            "exclusions": "Hosting, domains, plugins and CRM integrations are separately scoped.",
            "timeline": "Agree milestones after content and access. Two design revision rounds.",
            "support": "Recorded handover and thirty days of agreed defect support.",
            "terms": "Confirm payment stages and applicable taxes before work begins.",
            "currency": "CAD",
            "setup": "1800.50",
            "recurring": "",
            "period": "monthly",
        },
    )
    return {
        "meeting-pack": f"/bookings/{booking.id}/pack",
        "proposal-edit": f"/bookings/{booking.id}/proposal",
        "proposal": "/proposals/" + p.id,
        "proposals": "/pipeline/proposals",
    }
