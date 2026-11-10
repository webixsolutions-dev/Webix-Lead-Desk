"""Confirm actual meeting notes without treating a scheduled slot as a held meeting."""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.models import AuditEvent, User
from app.db.proposal_models import MeetingNote
from app.db.session import Database


def lock_booking(session: Session, booking_id: str, actor_id: str) -> Booking:
    """Serialize revisions on both supported databases and enforce owner authority."""
    actor = session.get(User, actor_id)
    if not actor or not actor.active or actor.role != "owner":
        raise ValueError("Only the owner can manage proposals")
    session.execute(update(Booking).where(Booking.id == booking_id).values(version=Booking.version))
    booking = session.get(Booking, booking_id)
    if not booking:
        raise ValueError("Booking not found")
    return booking


def latest_note(session: Session, booking_id: str) -> MeetingNote | None:
    """Resolve the last confirmed notes revision, never merge meetings."""
    return session.scalar(
        select(MeetingNote)
        .where(MeetingNote.booking_id == booking_id)
        .order_by(MeetingNote.version.desc())
        .limit(1)
    )


def confirm_notes(
    db: Database,
    actor_id: str,
    booking_id: str,
    booking_version: int,
    expected_version: int,
    text: str,
) -> MeetingNote:
    """Save confirmed facts as a new version; a future meeting cannot supply notes."""
    if not 20 <= len(text.strip()) <= 6000:
        raise ValueError("Record 20–6000 characters of confirmed meeting notes")
    with db.sessions.begin() as session:
        booking = lock_booking(session, booking_id, actor_id)
        if (
            booking.version != booking_version
            or booking.state != "completed"
            or booking.ends_at > utc_now()
        ):
            raise ValueError(
                "Complete the actual past meeting and reload its current version first"
            )
        version = (
            session.scalar(
                select(func.max(MeetingNote.version)).where(MeetingNote.booking_id == booking_id)
            )
            or 0
        )
        if version != expected_version:
            raise ValueError("Notes changed in another tab; reload")
        note = MeetingNote(
            booking_id=booking_id,
            booking_version=booking.version,
            version=version + 1,
            text=text.strip(),
            confirmed_by=actor_id,
        )
        session.add(note)
        session.flush()
        session.add(
            AuditEvent(actor_id=actor_id, action="meeting.notes_confirmed", entity_id=note.id)
        )
        return note
