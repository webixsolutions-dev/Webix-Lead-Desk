"""Reviewable observations that cannot publish instructions by themselves."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class LearningProposal(Base):
    """An immutable source observation, with a separate attributable review outcome."""

    __tablename__ = "learning_proposals"
    __table_args__ = (UniqueConstraint("source_kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(48), index=True)
    source_kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    suggestion: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    applied_version_id: Mapped[str | None] = mapped_column(ForeignKey("playbook_versions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
