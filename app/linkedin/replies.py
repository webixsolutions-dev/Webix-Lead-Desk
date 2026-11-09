"""Attributable manual reply capture and controlled company restart."""

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.linkedin_models import LinkedInProfile, Person, PersonOwnership, Touch
from app.db.models import AuditEvent
from app.db.session import Database
from app.journey.events import emit
from app.linkedin.coordination import lock_company, record_touch, stop_company
from app.linkedin.identity import require_staff


def log_reply(
    db: Database,
    person_id: str,
    actor: str,
    request_key: str,
    outcome: str,
    text: str,
    *,
    channel: str = "linkedin",
) -> None:
    """Log a real reply for the member's own person and stop all pending company work."""
    if outcome not in {
        "interested",
        "not_now",
        "not_interested",
        "stop",
        "needs_review",
        "meeting_booked",
    } or channel not in {"linkedin", "phone", "form"}:
        raise ValueError("Choose the observed reply outcome and channel")
    if not 2 <= len(text.strip()) <= 8000 or not 1 <= len(request_key) <= 128:
        raise ValueError("Record the actual reply or a factual call summary")
    with db.sessions.begin() as session:
        user = require_staff(session, actor)
        person = session.get(Person, person_id)
        ownership = session.get(PersonOwnership, person_id)
        profile = session.get(LinkedInProfile, ownership.profile_id) if ownership else None
        if not person or (user.role != "owner" and (not profile or profile.user_id != actor)):
            raise ValueError("This person belongs to another team member")
        lock_company(session, person.company_id)
        key = "manual_reply:" + person_id + ":" + request_key
        if session.scalar(select(Touch.id).where(Touch.event_key == key)):
            return
        record_touch(
            session,
            person.company_id,
            key,
            channel,
            "inbound",
            outcome,
            person_id=person_id,
            profile_id=profile.id if profile else None,
            actor=actor,
            text=text.strip(),
        )
        stop_company(session, person.company_id, outcome, channel, key, actor=actor)


def reopen_company(db: Database, company_id: str, actor: str, new_angle: str) -> None:
    """Owner can plan fresh work after a cooldown, never revive tasks or undo opt-outs."""
    if not 20 <= len(new_angle.strip()) <= 1000:
        raise ValueError("Record a genuinely new angle before planning another sequence")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        item = lock_company(session, company_id)
        if item.state != "cooldown" or not item.hold_until or item.hold_until > utc_now():
            raise ValueError("Only an expired cooldown can be reopened; opt-outs remain permanent")
        item.state, item.reason, item.hold_until = "active", "", None
        emit(
            session,
            company_id,
            "reopen:" + company_id + ":" + str(item.revision),
            "outreach_resumed",
            {"reason": new_angle.strip()},
            actor=actor,
        )
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.cooldown_reopened",
                entity_id=company_id,
                details={"new_angle": new_angle.strip()},
            )
        )
