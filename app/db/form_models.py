"""Immutable manual form copy with separate owner approval and recorded outcome."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class FormDraft(Base):
    """One reviewed form attempt per business; this app never submits external forms."""

    __tablename__ = "form_drafts"
    __table_args__ = (UniqueConstraint("company_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome_note: Mapped[str] = mapped_column(Text, default="")
