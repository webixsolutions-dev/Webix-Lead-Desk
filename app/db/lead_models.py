"""Campaigns, discovery provenance and global contact policy."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class Campaign(Base):
    """Editable targeting independent of a particular import."""

    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(120))
    services: Mapped[list[str]] = mapped_column(JSON)
    lane: Mapped[str] = mapped_column(String(16), default="client")
    country: Mapped[str] = mapped_column(String(2), default="CA", server_default="CA")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    followup_days: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CampaignRun(Base):
    """Immutable targeting snapshot for one source import."""

    __tablename__ = "campaign_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32))
    imported: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Company(Base):
    """Business identity independent of campaigns and shared website domains."""

    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(300), default="")
    website: Mapped[str] = mapped_column(String(2000), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    has_website: Mapped[str] = mapped_column(String(8), default="unknown", server_default="unknown")
    status: Mapped[str] = mapped_column(String(32), default="new")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanyDomain(Base):
    """Explicit reviewer confirmation for a proposed website match."""

    __tablename__ = "company_domains"
    __table_args__ = (UniqueConstraint("company_id", "domain"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(24), default="unverified")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CampaignLead(Base):
    """Campaign membership without duplicating outreach history."""

    __tablename__ = "campaign_leads"
    __table_args__ = (UniqueConstraint("campaign_id", "company_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)


class SourceRecord(Base):
    """Provenance with source-specific stable import identity."""

    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("source", "source_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(2000))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("campaign_runs.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Contact(Base):
    """Candidate email; import alone never establishes permission to send."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("company_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    role: Mapped[str] = mapped_column(String(120), default="")
    verification: Mapped[str] = mapped_column(String(24), default="unverified")
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dns_result: Mapped[str | None] = mapped_column(String(24))
    dns_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dns_address: Mapped[str | None] = mapped_column(String(254))
    allow_role: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ContactBasis(Base):
    """Reviewed publication/consent evidence with relevance and refresh expiry."""

    __tablename__ = "contact_bases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(String(2000))
    relevance: Mapped[str] = mapped_column(Text)
    no_solicitation: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SuppressionEntry(Base):
    """Minimal global stop record retained across campaigns and mailboxes."""

    __tablename__ = "suppression_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(300), unique=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ChannelReservation(Base):
    """Only one first-touch channel per business across campaigns."""

    __tablename__ = "channel_reservations"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
