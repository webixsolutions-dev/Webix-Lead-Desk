"""Immutable draft revisions, owner approvals and sender identity profiles."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class Mailbox(Base):
    """A sender identity; saving a profile neither connects it nor enables delivery."""

    __tablename__ = "mailboxes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    provider: Mapped[str] = mapped_column(String(24), default="titan")
    connection: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    lane: Mapped[str] = mapped_column(String(16))
    brand: Mapped[str] = mapped_column(String(120))
    sender_name: Mapped[str] = mapped_column(String(120))
    mailing_address: Mapped[str] = mapped_column(String(500))
    contact_details: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Conversation(Base):
    """One outreach conversation per business across campaigns and sender mailboxes."""

    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), unique=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"))
    state: Mapped[str] = mapped_column(String(24), default="draft")
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_message_id: Mapped[str | None] = mapped_column(String(255))
    last_message_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Draft(Base):
    """An immutable content revision; editing supersedes it and invalidates approval."""

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("conversation_id", "step", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(
        String(20), default="first_touch", server_default="first_touch"
    )
    sequence_step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    step: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class DraftApproval(Base):
    """An owner's approval of one exact content fingerprint, never a general send grant."""

    __tablename__ = "draft_approvals"
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PlaybookVersion(Base):
    """Owner-authored guidance with append-only revisions and explicit scope."""

    __tablename__ = "playbook_versions"
    __table_args__ = (UniqueConstraint("scope", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[int] = mapped_column(Integer)
    guidance: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Offer(Base):
    """An editable commercial service; no prices are invented or inserted into first touch."""

    __tablename__ = "offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    setup_cad_cents: Mapped[int] = mapped_column(Integer)
    recurring_cad_cents: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ProofCard(Base):
    """An owner-confirmed case fact with provenance; no seeded performance claims."""

    __tablename__ = "proof_cards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160))
    fact: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2000))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class LocalTeamMember(Base):
    """An owner-recorded person available locally; never inferred from a website."""

    __tablename__ = "local_team_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(120), default="")
    country: Mapped[str] = mapped_column(String(2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
