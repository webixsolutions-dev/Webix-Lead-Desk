"""Persistent human schedules, quota accounting and cross-channel daily fences."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class LinkedInPolicy(Base):
    """Per-profile serialized limits; quotas survive processes and machines."""

    __tablename__ = "linkedin_policies"
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), primary_key=True)
    limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    planned_day: Mapped[str | None] = mapped_column(String(10))


class LinkedInSequence(Base):
    """Explicit sequence progress; acceptance never implies an owner message approval."""

    __tablename__ = "linkedin_sequences"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(24), default="assigned")
    next_action: Mapped[str] = mapped_column(String(24), default="invite")
    next_due: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    invited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    hold_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class LinkedInQuota(Base):
    """Conservative daily reservations, never reset when a worker restarts."""

    __tablename__ = "linkedin_quotas"
    __table_args__ = (UniqueConstraint("task_id", "local_day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("linkedin_tasks.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    local_day: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PersonDay(Base):
    """One real outbound touch per named person per Calgary day across transports."""

    __tablename__ = "person_days"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    local_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    action_key: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(24))
    reserved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
