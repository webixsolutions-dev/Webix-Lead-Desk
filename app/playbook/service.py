"""Versioned owner guidance with optimistic edits; imported text cannot publish itself."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.lead_models import Campaign
from app.db.models import AuditEvent, User
from app.db.outreach_models import PlaybookVersion
from app.db.session import Database


def current_guidance(db: Database, campaign_id: str | None = None) -> list[PlaybookVersion]:
    """Return global guidance and the current campaign-specific override, if present."""
    result: list[PlaybookVersion] = []
    with db.sessions() as session:
        for scope in ["global"] + ([campaign_id] if campaign_id else []):
            item = session.scalar(
                select(PlaybookVersion)
                .where(PlaybookVersion.scope == scope)
                .order_by(PlaybookVersion.version.desc())
                .limit(1)
            )
            if item:
                result.append(item)
    return result


def publish(
    db: Database, scope: str, guidance: str, expected: int, actor_id: str
) -> PlaybookVersion:
    """Append guidance only if the owner edited the current version."""
    if not 10 <= len(guidance.strip()) <= 12000 or expected < 0:
        raise ValueError("Guidance must contain 10–12,000 characters")
    try:
        with db.sessions.begin() as session:
            actor = session.get(User, actor_id)
            if not actor or actor.role != "owner" or not actor.active:
                raise ValueError("Only the owner can publish guidance")
            if scope != "global" and session.get(Campaign, scope) is None:
                raise ValueError("Choose global guidance or an existing campaign")
            current = session.scalar(
                select(PlaybookVersion)
                .where(PlaybookVersion.scope == scope)
                .order_by(PlaybookVersion.version.desc())
                .limit(1)
            )
            if (current.version if current else 0) != expected:
                raise ValueError("Guidance changed. Reload before publishing.")
            version = PlaybookVersion(
                scope=scope, version=expected + 1, guidance=guidance.strip(), created_by=actor_id
            )
            session.add(version)
            session.add(AuditEvent(actor_id=actor_id, action="playbook.published", entity_id=scope))
            session.flush()
            return version
    except IntegrityError as error:
        raise ValueError("Another editor published first. Reload this page.") from error
