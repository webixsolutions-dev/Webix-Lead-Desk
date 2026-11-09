"""Append-only member observations and reviewed acquisition-risk baselines."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class ProfileObservation(Base):
    """A real member/owner's SSI or platform-warning observation, never inferred."""

    __tablename__ = "linkedin_profile_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), index=True)
    ssi: Mapped[int | None] = mapped_column(Integer)
    warning: Mapped[str] = mapped_column(String(1000), default="")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PostObservation(Base):
    """One cumulative manually entered analytics snapshot; snapshots are not summed."""

    __tablename__ = "linkedin_post_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(ForeignKey("linkedin_posts.id"), index=True)
    counts: Mapped[dict[str, int]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(1000))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ProfileRiskReview(Base):
    """Owner resume decision starts a new evaluation cohort without erasing history."""

    __tablename__ = "linkedin_profile_risk_reviews"
    profile_id: Mapped[str] = mapped_column(ForeignKey("linkedin_profiles.id"), primary_key=True)
    review_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(String(1000), default="")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
