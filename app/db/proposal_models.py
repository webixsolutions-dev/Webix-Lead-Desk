"""Private meeting snapshots, confirmed notes and immutable proposal revisions."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class MeetingPack(Base):
    """A bounded preparation snapshot captured with each booking edit."""

    __tablename__ = "meeting_packs"
    __table_args__ = (UniqueConstraint("booking_id", "booking_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"), index=True)
    booking_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MeetingNote(Base):
    """Owner-confirmed notes; later corrections create a new immutable version."""

    __tablename__ = "meeting_notes"
    __table_args__ = (UniqueConstraint("booking_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"), index=True)
    booking_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Proposal(Base):
    """One exact recipient, identity, notes version and commercial document."""

    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("booking_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"), index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("meeting_notes.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_reference: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ProposalFollowup(Base):
    """Day 2, 5 and 9 owner review reminders, never permission to send."""

    __tablename__ = "proposal_followups"
    __table_args__ = (UniqueConstraint("proposal_id", "day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str] = mapped_column(String(1000), default="")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
