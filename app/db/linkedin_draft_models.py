"""Exact LinkedIn copy revisions, approvals and independent human reply opportunities."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class LinkedInDraft(Base):
    """Immutable content and routing; editing always creates a new revision."""

    __tablename__ = "linkedin_drafts"
    __table_args__ = (UniqueConstraint("task_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("linkedin_tasks.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="draft")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LinkedInApproval(Base):
    """One owner decision bound to the exact revision hash."""

    __tablename__ = "linkedin_approvals"
    draft_id: Mapped[str] = mapped_column(ForeignKey("linkedin_drafts.id"), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PersonPipeline(Base):
    """A reply opportunity without an invented email conversation or mailbox."""

    __tablename__ = "person_pipeline"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(32), default="Replied")
    note: Mapped[str] = mapped_column(String(2000), default="")
    next_due: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
