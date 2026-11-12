"""Persist provider pauses so a failed request cannot spend through a queued batch."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent

PROVIDERS = {
    "openai": "OpenAI",
    "google_places": "Google Places",
    "pagespeed": "PageSpeed Insights",
}


def paused_event(session: Session, provider: str) -> AuditEvent | None:
    """Find the most recent pause until the owner explicitly acknowledges it."""
    event = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.entity_id == provider,
            AuditEvent.action.in_(["api.paused", "api.resumed"]),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    return event if event and event.action == "api.paused" else None


def pause(session: Session, provider: str, reference: str) -> None:
    """Keep the provider and request ID, without storing provider payloads or secrets."""
    session.add(
        AuditEvent(action="api.paused", entity_id=provider, details={"reference": reference})
    )
