"""Durable delivery reservations, mailbox health, encrypted credentials and inbox events."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class IntegrationSecret(Base):
    """Encrypted provider material; the encryption key never lives in this database."""

    __tablename__ = "integration_secrets"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MailboxState(Base):
    """Sender readiness, spacing and a durable ramp start independent of process uptime."""

    __tablename__ = "mailbox_states"
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), primary_key=True)
    readiness: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=10)
    ramp_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_send_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    paused_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pause_reason: Mapped[str] = mapped_column(String(64), default="")
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class MailboxDay(Base):
    """Reserved daily capacity and distinct bounces in the Calgary calendar day."""

    __tablename__ = "mailbox_days"
    __table_args__ = (UniqueConstraint("mailbox_id", "local_day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"))
    local_day: Mapped[str] = mapped_column(String(10))
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    bounces: Mapped[int] = mapped_column(Integer, default=0)


class InboxCursor(Base):
    """UID checkpoint; dispatch requires a recently completed inbox sync."""

    __tablename__ = "inbox_cursors"
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), primary_key=True)
    uidvalidity: Mapped[str] = mapped_column(String(32), default="")
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))


class SendIntent(Base):
    """One durable transmission decision per conversation step, with a stable Message-ID."""

    __tablename__ = "send_intents"
    __table_args__ = (UniqueConstraint("conversation_id", "step"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), unique=True)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True)
    wire_message: Mapped[str | None] = mapped_column(Text)
    approval_digest: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    hold_reason: Mapped[str] = mapped_column(String(100), default="")


class SendAttempt(Base):
    """Redacted provider attempt history; full bodies remain only in private message records."""

    __tablename__ = "send_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intent_id: Mapped[str] = mapped_column(ForeignKey("send_intents.id"), index=True)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str] = mapped_column(String(24), default="started")
    error_code: Mapped[str | None] = mapped_column(String(64))


class InboxMessage(Base):
    """A bounded plain-text reply, deduplicated even after UIDVALIDITY changes."""

    __tablename__ = "inbox_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider_key: Mapped[str] = mapped_column(String(80))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(24), default="needs_review")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
