"""Conservative automatic holds and fortnightly proposals requiring owner review."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.db.learning_models import LearningProposal
from app.db.linkedin_metric_models import ProfileRiskReview
from app.db.linkedin_models import LinkedInProfile, Touch
from app.db.models import AuditEvent, User
from app.db.session import Database
from app.learning.capture import capture
from app.linkedin.metrics import angle_rows, candidate_winners, conversions, first_touches
from app.linkedin.quotas import lock_policy, lock_registry
from app.operations.control import maintenance_enabled
from app.sending.window import CALGARY


def evaluate_pause(session: Session, profile_id: str, now: datetime) -> bool:
    """Pause at fewer than 20% acceptances after 50 unique invitations since the last review."""
    lock_policy(session, profile_id, now)
    profile = session.get(LinkedInProfile, profile_id, populate_existing=True)
    if not profile or profile.status != "active":
        return True
    review = session.get(ProfileRiskReview, profile_id)
    start = (
        review.review_after if review and review.review_after else datetime(1970, 1, 1, tzinfo=UTC)
    )
    rows = list(
        session.scalars(
            select(Touch)
            .options(defer(Touch.text))
            .where(
                Touch.profile_id == profile_id,
                Touch.channel == "linkedin",
                Touch.occurred_at >= start,
                Touch.occurred_at <= now,
            )
        )
    )
    cohort = first_touches(rows, "invite", start, now + timedelta(microseconds=1))
    accepted = len(conversions(rows, cohort, "accepted"))
    if len(cohort) < 50 or accepted * 5 >= len(cohort):
        return False
    profile.status = "paused"
    reason = f"Acceptance review: {accepted}/{len(cohort)} unique invitations accepted; below 20%."
    if not review:
        review = ProfileRiskReview(profile_id=profile_id)
        session.add(review)
    review.reason, review.updated_at = reason, now
    session.add(
        AuditEvent(
            action="linkedin.acceptance_paused",
            entity_id=profile_id,
            details={
                "invited": len(cohort),
                "accepted": accepted,
                "cohort_after": start.isoformat(),
            },
        )
    )
    return True


def closed_fortnight(now: datetime) -> tuple[datetime, datetime]:
    """Use stable fourteen-day Calgary periods ending on Monday, never a moving window."""
    day = now.astimezone(CALGARY).date()
    anchor = date(2026, 1, 5)
    end = anchor + timedelta(days=((day - anchor).days // 14) * 14)
    end_at = datetime.combine(end, time.min, CALGARY)
    return end_at - timedelta(days=14), end_at


def propose(db: Database, now: datetime) -> None:
    """Capture one inert fortnight summary; no model call and no automatic guidance update."""
    start, end = closed_fortnight(now)
    with db.sessions.begin() as session:
        if maintenance_enabled(session):
            return
        lock_registry(session)
        source_id = "linkedin-fortnight:" + end.date().isoformat()
        if session.scalar(
            select(LearningProposal.id).where(
                LearningProposal.source_kind == "outcome", LearningProposal.source_id == source_id
            )
        ):
            return
        actor = session.scalar(
            select(User)
            .where(User.role == "owner", User.active.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        if not actor:
            return
        rows = list(
            session.scalars(
                select(Touch)
                .options(defer(Touch.text))
                .where(
                    Touch.channel == "linkedin", Touch.occurred_at >= start, Touch.occurred_at < end
                )
            )
        )
        if not any(row.direction == "outbound" for row in rows):
            return
        profiles = list(session.scalars(select(LinkedInProfile)))
        evidence: dict[str, object] = {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "source": "Human-confirmed LinkedIn touches; not platform estimates",
        }
        summaries: list[dict[str, Any]] = []
        for profile in profiles:
            own = [row for row in rows if row.profile_id == profile.id]
            invites = first_touches(own, "invite", start, end)
            messages = first_touches(own, "message1", start, end)
            accepted = len(conversions(own, invites, "accepted"))
            replies = len(conversions(own, messages, "reply", horizon_days=30))
            positive = len(conversions(own, messages, "positive", horizon_days=30))
            summaries.append(
                {
                    "profile_id": profile.id,
                    "invites": len(invites),
                    "accepted": accepted,
                    "first_messages": len(messages),
                    "replies": replies,
                    "positive": positive,
                }
            )
        evidence["profiles"] = summaries
        total = sum(row["first_messages"] for row in summaries)
        positive = sum(row["positive"] for row in summaries)
        suggestion = (
            "For LinkedIn only: review the attached completed fortnight before choosing the next "
            f"targeting or copy test. Recorded {total} first-message contacts "
            f"and {positive} positive "
            "replies within this period. Keep one comparable change per test and use only reviewed "
            "business facts. Do not declare a winning angle until every compared variant has at "
            "least 50 distinct contacts. Owner: replace this draft guidance with the specific "
            "lesson the evidence supports before applying."
        )
        angles = angle_rows(session, rows)
        leaders = candidate_winners(angles)
        evidence["angle_comparisons"] = [
            {
                key: row[key]
                for key in ("angle", "segment", "warm", "contacts", "replied", "positive")
            }
            for row in angles
        ]
        if leaders:
            suggestion += (
                " Observed candidates for the next controlled test: "
                + "; ".join(f"{row['angle']} ({row['segment']}, {row['warm']})" for row in leaders)[
                    :1500
                ]
                + ". These are observational results, not proven causation."
            )
        capture(
            session,
            "outcome",
            "linkedin-fortnight:" + end.date().isoformat(),
            "global",
            actor.id,
            "LinkedIn fortnight review · " + start.date().isoformat(),
            suggestion,
            evidence,
        )


def tick(db: Database, now: datetime) -> None:
    """Evaluate member health and offer one completed-period learning review each worker tick."""
    with db.sessions.begin() as session:
        if maintenance_enabled(session):
            return
        for profile_id in session.scalars(
            select(LinkedInProfile.id).where(LinkedInProfile.status == "active")
        ):
            evaluate_pause(session, profile_id, now)
    propose(db, now)
