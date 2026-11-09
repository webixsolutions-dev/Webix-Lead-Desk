"""Durable search reservations, transparent scoring and human call records."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class SearchBucket(Base):
    """Atomic provider quota for a Calgary calendar day or month."""

    __tablename__ = "search_buckets"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    committed: Mapped[int] = mapped_column(Integer, default=0)


class SearchRecord(Base):
    """One paid search with retained reservations after uncertain provider outcomes."""

    __tablename__ = "search_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    cache_key: Mapped[str] = mapped_column(String(100), index=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    provider: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="reserved")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ai_call_id: Mapped[str | None] = mapped_column(ForeignKey("ai_calls.id"))
    reserved: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ScorePolicy(Base):
    """Immutable owner-edited scoring weights and band thresholds."""

    __tablename__ = "score_policies"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LeadScore(Base):
    """An immutable score snapshot with all supporting reasons and evidence links."""

    __tablename__ = "lead_scores"
    __table_args__ = (UniqueConstraint("company_id", "campaign_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(24))
    breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(2000))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CallRecord(Base):
    """A staff-entered call outcome; never an instruction to dial or send."""

    __tablename__ = "call_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str] = mapped_column(String(24))
    notes: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    consent_note: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CallNote(Base):
    """Unapproved model cleanup linked to the original human notes."""

    __tablename__ = "call_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    call_id: Mapped[str] = mapped_column(ForeignKey("call_records.id"), unique=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LeadEvent(Base):
    """Append-only, idempotent facts from which the journey is projected."""

    __tablename__ = "lead_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_key: Mapped[str] = mapped_column(String(180), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    item_url: Mapped[str] = mapped_column(String(2000), default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
