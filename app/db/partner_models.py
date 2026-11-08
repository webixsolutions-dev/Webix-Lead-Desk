"""Partner jurisdiction reviews and conservative new-business draft capacity."""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime


class PartnerReview(Base):
    """An owner's recorded country and cross-border permission review."""

    __tablename__ = "partner_reviews"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    country: Mapped[str] = mapped_column(String(2))
    source_url: Mapped[str] = mapped_column(String(2000))
    note: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PartnerDay(Base):
    """One shared five-business capacity counter across all partner campaigns."""

    __tablename__ = "partner_days"
    local_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class PartnerSlot(Base):
    """A business consumes one slot, including when a later model attempt fails."""

    __tablename__ = "partner_slots"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    local_day: Mapped[str] = mapped_column(ForeignKey("partner_days.local_day"))
