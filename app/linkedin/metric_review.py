"""Human analytics snapshots and guarded review of paused real-member profiles."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.linkedin_metric_models import PostObservation, ProfileObservation, ProfileRiskReview
from app.db.linkedin_models import LinkedInProfile
from app.db.linkedin_post_models import LinkedInPost
from app.db.models import AuditEvent
from app.db.session import Database
from app.linkedin.identity import require_staff
from app.linkedin.quotas import lock_policy


class PostCounts(BaseModel):
    """Cumulative observed counts, never treated as independent added totals."""

    model_config = ConfigDict(extra="forbid")
    impressions: int = Field(ge=0, le=100_000_000)
    reactions: int = Field(ge=0, le=100_000_000)
    comments: int = Field(ge=0, le=100_000_000)
    profile_views: int = Field(ge=0, le=100_000_000)
    target_connections: int = Field(ge=0, le=100_000_000)
    target_dms: int = Field(ge=0, le=100_000_000)


def access(session: Session, profile_id: str, actor: str) -> LinkedInProfile:
    """Allow observation and review of paused profiles without allowing their execution."""
    user = require_staff(session, actor)
    profile = session.get(LinkedInProfile, profile_id)
    if not profile or (user.role != "owner" and profile.user_id != actor):
        raise ValueError("Member profile unavailable")
    return profile


def record_profile(
    db: Database,
    profile_id: str,
    actor: str,
    key: str,
    ssi: int | None,
    warning: str,
    restricted: bool,
) -> None:
    """Save one human SSI/warning observation; a reported restriction immediately pauses work."""
    key = str(UUID(key))
    if (
        (ssi is not None and not 0 <= ssi <= 100)
        or len(warning.strip()) > 1000
        or (restricted and len(warning.strip()) < 10)
    ):
        raise ValueError("SSI is 0–100; record a factual note for a restriction")
    with db.sessions.begin() as session:
        profile = access(session, profile_id, actor)
        lock_policy(session, profile_id, utc_now())
        existing = session.get(ProfileObservation, key)
        if existing:
            if existing.profile_id != profile_id or existing.actor_id != actor:
                raise ValueError("Observation key already belongs to another record")
            return
        session.add(
            ProfileObservation(
                id=key,
                profile_id=profile_id,
                ssi=ssi,
                warning=warning.strip(),
                restricted=restricted,
                actor_id=actor,
            )
        )
        if restricted:
            profile.status = "restricted"
            session.add(
                AuditEvent(
                    actor_id=actor, action="linkedin.restriction_reported", entity_id=profile_id
                )
            )


def record_post(
    db: Database, post_id: str, actor: str, key: str, counts: PostCounts, note: str
) -> None:
    """Record the latest visible totals and attribution note for a confirmed published post."""
    key = str(UUID(key))
    if not 10 <= len(note.strip()) <= 1000:
        raise ValueError("Record where the counts came from and how target buyers were identified")
    with db.sessions.begin() as session:
        post = session.get(LinkedInPost, post_id)
        if not post or post.state != "published":
            raise ValueError("Record metrics only for a confirmed published post")
        access(session, post.profile_id, actor)
        lock_policy(session, post.profile_id, utc_now())
        existing = session.get(PostObservation, key)
        if existing:
            if existing.post_id != post_id or existing.actor_id != actor:
                raise ValueError("Observation key already belongs to another record")
            return
        session.add(
            PostObservation(
                id=key,
                post_id=post_id,
                counts=counts.model_dump(),
                note=note.strip(),
                actor_id=actor,
            )
        )


def resume(db: Database, profile_id: str, actor: str, note: str, now: datetime) -> None:
    """Owner resumes only after reviewing targeting or resolving the reported restriction."""
    if not 20 <= len(note.strip()) <= 1000:
        raise ValueError("Record the reviewed change or confirmed restriction resolution")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        profile = access(session, profile_id, actor)
        lock_policy(session, profile_id, now)
        session.refresh(profile)
        if profile.status not in {"paused", "restricted"}:
            raise ValueError("Only a paused or restricted profile needs a resume review")
        review = session.get(ProfileRiskReview, profile_id)
        if not review:
            review = ProfileRiskReview(profile_id=profile_id)
            session.add(review)
        review.review_after, review.reason, review.reviewed_by, review.updated_at = (
            now,
            note.strip(),
            actor,
            now,
        )
        profile.status = "active"
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.profile_resumed",
                entity_id=profile_id,
                details={"reason": note.strip(), "new_cohort_after": now.isoformat()},
            )
        )
