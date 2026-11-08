"""Phase 1 tables only; future campaign and sending schemas are deferred."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.clock import utc_now


def new_id() -> str:
    """Generate a portable UUID represented as text."""
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Normalize timestamps across SQLite's naive storage and PostgreSQL."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reject naive timestamps and normalize accepted values to UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        normalized = value.astimezone(UTC)
        return normalized.replace(tzinfo=None) if dialect.name == "sqlite" else normalized

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Restore awareness when SQLite returns a naive timestamp."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    """Base for all versioned database entities."""


class User(Base):
    """An owner or reviewer with a locally bootstrapped account."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'reviewer')", name="ck_users_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="reviewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class UserSession(Base):
    """A revocable session whose bearer token is never stored in plaintext."""

    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class LoginThrottle(Base):
    """A hashed account or source-address login rate-limit bucket."""

    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class WorkspaceSetting(Base):
    """Versioned non-secret workspace preferences."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AuditEvent(Base):
    """Redacted security and settings events, never message payloads."""

    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)


class WorkerInstance(Base):
    """Singleton worker lease with a replaceable process owner."""

    __tablename__ = "worker_instances"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Job(Base):
    """Durable work item with a unique business key and a fenced claim."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("state IN ('queued','running','succeeded','failed')", name="ck_jobs_state"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unique_key: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str | None] = mapped_column(Text)


class JobAttempt(Base):
    """Attempt history with safe status codes rather than exception contents."""

    __tablename__ = "job_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    claim_token: Mapped[str] = mapped_column(String(36), unique=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str] = mapped_column(String(32), default="running")
