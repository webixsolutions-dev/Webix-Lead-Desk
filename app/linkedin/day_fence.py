"""One-person/day reservations shared by email and future approved human LinkedIn tasks."""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.lead_models import Contact
from app.db.linkedin_models import Person, Touch
from app.db.linkedin_queue_models import PersonDay
from app.linkedin.coordination import lock_company
from app.linkedin.timing import local_day


def reserve_touch(
    session: Session, person_id: str, action_key: str, channel: str, now: datetime
) -> bool:
    """Keep an uncertain action's daily reservation; only its identical retry may reuse it."""
    person = session.get(Person, person_id)
    if not person:
        raise ValueError("Verified person not found")
    lock_company(session, person.company_id)
    day = local_day(now)
    existing = session.get(PersonDay, (person_id, day))
    if existing:
        return existing.action_key == action_key and existing.channel == channel
    # Historical or manually recorded actions are also respected.
    from datetime import time

    from app.sending.window import CALGARY

    start = datetime.combine(now.astimezone(CALGARY).date(), time(), CALGARY)
    if session.scalar(
        select(Touch.id)
        .where(
            Touch.person_id == person_id,
            Touch.direction == "outbound",
            Touch.channel.in_(["email", "linkedin"]),
            Touch.occurred_at >= start,
            Touch.occurred_at <= now,
        )
        .limit(1)
    ):
        return False
    session.add(
        PersonDay(
            person_id=person_id,
            local_day=day,
            action_key=action_key,
            channel=channel,
            reserved_at=now,
        )
    )
    session.flush()
    return True


def reserve_email(session: Session, contact_id: str, intent_id: str, now: datetime) -> bool:
    """Resolve late-imported personal email identities; generic company email remains separate."""
    contact = session.get(Contact, contact_id)
    if not contact:
        return False
    person = session.scalar(
        select(Person).where(
            Person.company_id == contact.company_id,
            or_(Person.contact_id == contact_id, Person.personal_email == contact.email),
        )
    )
    if not person:
        return True
    if person.contact_id is None:
        person.contact_id = contact_id
    return reserve_touch(session, person.id, "email:" + intent_id, "email", now)
