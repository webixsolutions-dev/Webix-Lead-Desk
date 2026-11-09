"""Durable member posting, exact approvals and private chunked media."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class PostingConnection(Base):
    """An explicitly confirmed OAuth member; token lives in the encrypted vault."""

    __tablename__ = "linkedin_posting_connections"
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), unique=True)
    member_name: Mapped[str] = mapped_column(String(200))
    profile_url: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(24), default="confirm")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PostingOAuthState(Base):
    """One-use session-bound state; excluded from portable backups."""

    __tablename__ = "linkedin_oauth_states"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PostMedia(Base):
    """Bounded durable private media; base64 chunks keep portable backups JSON-only."""

    __tablename__ = "linkedin_post_media"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    size: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="uploading")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PostMediaChunk(Base):
    """Immutable idempotent chunks, each below hosted request and response limits."""

    __tablename__ = "linkedin_post_media_chunks"
    media_id: Mapped[str] = mapped_column(ForeignKey("linkedin_post_media.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[str] = mapped_column(Text)


class LinkedInPost(Base):
    """A calendar slot; revisions and approvals are separate immutable records."""

    __tablename__ = "linkedin_posts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    topic: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    state: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    media_urn: Mapped[str] = mapped_column(String(200), default="")
    published_url: Mapped[str] = mapped_column(String(500), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PostRevision(Base):
    """Immutable full outgoing snapshot, including author, schedule and media hash."""

    __tablename__ = "linkedin_post_revisions"
    __table_args__ = (UniqueConstraint("post_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(ForeignKey("linkedin_posts.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    body_key: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PostApproval(Base):
    """Owner approval of an exact immutable revision."""

    __tablename__ = "linkedin_post_approvals"
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("linkedin_post_revisions.id"), primary_key=True
    )
    digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
