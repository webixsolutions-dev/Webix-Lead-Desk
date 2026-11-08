"""Owner-reviewed pilot cohort and conservative first-touch capacity."""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime


class Pilot(Base):
    """Singleton pilot gate; completion records acceptance without approving any messages."""

    __tablename__ = "pilots"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PilotMember(Base):
    """One source-reviewed company in the ten-business Calgary pilot."""

    __tablename__ = "pilot_members"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    sector: Mapped[str] = mapped_column(String(24))
    review_note: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PilotReservation(Base):
    """Each first-touch business consumes one of five pilot places, even if uncertain."""

    __tablename__ = "pilot_reservations"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("send_intents.id"), unique=True)
    reserved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
