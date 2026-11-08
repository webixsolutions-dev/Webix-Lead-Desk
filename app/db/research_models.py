"""Auditable research facts, manual observations and conservative AI spending."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class WebsiteAudit(Base):
    """An immutable bounded research result, identified by its job for recovery."""

    __tablename__ = "website_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    website: Mapped[str] = mapped_column(String(2000))
    state: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Evidence(Base):
    """A source-addressed fact, never a website's instruction to the agent."""

    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    audit_id: Mapped[str | None] = mapped_column(ForeignKey("website_audits.id"))
    kind: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(String(2000))
    facts: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Assessment(Base):
    """A reviewable service hypothesis referencing evidence IDs only."""

    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    origin: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class BudgetAccount(Base):
    """Atomic lifetime workspace allowance in USD millionths; zero by default."""

    __tablename__ = "ai_budget_accounts"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    allowance: Mapped[int] = mapped_column(Integer, default=0)
    committed: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    verified_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AICall(Base):
    """One provider call reservation; uncertain outcomes are never retried blindly."""

    __tablename__ = "ai_calls"
    __table_args__ = (UniqueConstraint("request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_key: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default="reserved")
    reserved: Mapped[int] = mapped_column(Integer)
    charged: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
