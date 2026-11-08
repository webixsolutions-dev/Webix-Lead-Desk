"""Capture attributable internal feedback as inert proposals, never executable instructions."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.learning_models import LearningProposal
from app.db.models import User
from app.db.outreach_models import Draft


def capture(
    session: Session,
    kind: str,
    source_id: str,
    scope: str,
    actor_id: str,
    title: str,
    suggestion: str,
    evidence: dict[str, Any],
) -> None:
    """Save at most one proposal per internal event; incoming content never calls this function."""
    actor = session.get(User, actor_id)
    if not actor or not actor.active or kind not in {"edit", "rejection", "outcome"}:
        return
    if session.scalar(
        select(LearningProposal.id).where(
            LearningProposal.source_kind == kind, LearningProposal.source_id == source_id
        )
    ):
        return
    session.add(
        LearningProposal(
            scope=scope,
            source_kind=kind,
            source_id=source_id,
            title=title,
            suggestion=suggestion[:6000],
            evidence=evidence,
            created_by=actor_id,
        )
    )


def capture_edit(session: Session, previous: Draft | None, current: Draft, actor_id: str) -> None:
    """Offer a revised first-touch example for explicit owner review, without generalizing facts."""
    if not previous or current.step != 0 or previous.snapshot["body"] == current.snapshot["body"]:
        return
    capture(
        session,
        "edit",
        current.id,
        current.snapshot["campaign_id"],
        actor_id,
        "Remember a revised first-contact example",
        "Use this reviewed example for tone and structure only. Replace all business-specific "
        "facts and questions with evidence for the next prospect.\n\nPreferred example:\n"
        + current.snapshot["body"],
        {
            "previous_draft_id": previous.id,
            "current_draft_id": current.id,
            "before": previous.snapshot["body"],
            "after": current.snapshot["body"],
        },
    )
