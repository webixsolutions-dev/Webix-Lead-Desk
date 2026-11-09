"""Verified people, real member routing and durable cross-channel coordination."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class LinkedInProfile(Base):
    """One real member per staff account; passwords and cookies are never collected."""

    __tablename__ = "linkedin_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    profile_url: Mapped[str] = mapped_column(String(500), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    voice_card: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Person(Base):
    """Human-confirmed professional identity; a search suggestion is not a person."""

    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(160))
    decision_maker: Mapped[bool] = mapped_column(Boolean, default=False)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), unique=True)
    personal_email: Mapped[str | None] = mapped_column(String(254), unique=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), unique=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    presence: Mapped[str] = mapped_column(String(16), default="unknown")
    source_url: Mapped[str] = mapped_column(String(2000))
    verification_note: Mapped[str] = mapped_column(Text)
    verified_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PersonIdentity(Base):
    """Unique aliases prevent URL, email and name/domain duplicates independently."""

    __tablename__ = "person_identities"
    key: Mapped[str] = mapped_column(String(600), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)


class PersonCandidate(Base):
    """Source-backed proposal requiring an explicit human identity decision."""

    __tablename__ = "person_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    search_id: Mapped[str] = mapped_column(ForeignKey("search_records.id"))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="proposed")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanyCoordination(Base):
    """Serializes company routing and stops without weakening global suppression."""

    __tablename__ = "company_coordination"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("linkedin_profiles.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="active")
    reason: Mapped[str] = mapped_column(String(500), default="")
    hold_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PersonOwnership(Base):
    """One current owner, with lock expiry preserved after a sequence completes."""

    __tablename__ = "person_ownership"
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    warm: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(String(500), default="")


class LinkedInTask(Base):
    """Persistent human task foundation; workers never execute its outreach action."""

    __tablename__ = "linkedin_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    action: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Touch(Base):
    """One attributable cross-channel action or reply, never interpreted as instructions."""

    __tablename__ = "touches"
    __table_args__ = (UniqueConstraint("event_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_key: Mapped[str] = mapped_column(String(200))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("linkedin_profiles.id"))
    channel: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
