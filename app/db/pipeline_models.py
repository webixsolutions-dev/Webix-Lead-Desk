"""Sales opportunities, reviewed call handoffs and immutable concept revisions."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class Opportunity(Base):
    """One internal sales record per conversation; inbox content never approves a stage."""

    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)
    stage: Mapped[str] = mapped_column(String(24), default="Replied", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    summary: Mapped[str] = mapped_column(Text, default="Review the reply and agree a next step.")
    next_action: Mapped[str] = mapped_column(String(500), default="Review the reply")
    next_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    meeting_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    meeting_url: Mapped[str] = mapped_column(String(2000), default="")
    setup_cad_cents: Mapped[int] = mapped_column(Integer, default=0)
    recurring_cad_cents: Mapped[int] = mapped_column(Integer, default=0)
    outcome_note: Mapped[str] = mapped_column(Text, default="")
    last_reply_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PipelineEvent(Base):
    """A readable stage/change audit with attributable human decisions."""

    __tablename__ = "pipeline_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    stage: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class DemoVersion(Base):
    """An immutable concept; new edits create revisions without changing an already shared page."""

    __tablename__ = "demo_versions"
    __table_args__ = (UniqueConstraint("company_id", "kind", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    kind: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="draft")
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    share_url: Mapped[str | None] = mapped_column(String(2200))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
