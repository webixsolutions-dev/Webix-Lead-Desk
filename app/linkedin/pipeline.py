"""Human-owned LinkedIn sales cards alongside the existing email pipeline."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.lead_models import Company
from app.db.linkedin_draft_models import PersonPipeline
from app.db.linkedin_models import Person, Touch
from app.db.models import AuditEvent
from app.db.session import Database
from app.linkedin.identity import require_staff
from app.pipeline.service import STAGES, TRANSITIONS


def cards(db: Database) -> list[dict[str, Any]]:
    """Expose observed replies, with a default review stage for historical records."""
    with db.sessions() as session:
        people = list(
            session.scalars(
                select(Person).where(
                    Person.id.in_(
                        select(Touch.person_id).where(
                            Touch.direction == "inbound", Touch.channel == "linkedin"
                        )
                    )
                )
            )
        )
        result = []
        for person in people:
            card = session.get(PersonPipeline, person.id)
            reply = session.scalar(
                select(Touch)
                .where(Touch.person_id == person.id, Touch.direction == "inbound")
                .order_by(Touch.occurred_at.desc())
                .limit(1)
            )
            result.append(
                {
                    "person": person,
                    "company": session.get(Company, person.company_id),
                    "reply": reply,
                    "stage": card.stage if card else "Replied",
                    "card": card,
                }
            )
        return result


def update_card(
    db: Database,
    person_id: str,
    actor: str,
    stage: str,
    note: str,
    version: int,
    next_due: datetime | None,
) -> None:
    """Record a human stage decision with optimistic concurrency and an audit trail."""
    if stage not in STAGES or not 10 <= len(note.strip()) <= 2000:
        raise ValueError("Choose a stage and a factual next-step note")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        person = session.get(Person, person_id)
        if not person:
            raise ValueError("Person not found")
        from app.linkedin.coordination import lock_company

        lock_company(session, person.company_id)
        if not session.scalar(
            select(Touch.id)
            .where(
                Touch.person_id == person_id,
                Touch.channel == "linkedin",
                Touch.direction == "inbound",
            )
            .limit(1)
        ):
            raise ValueError("Record the actual reply first")
        card = session.get(PersonPipeline, person_id)
        if not card:
            card = PersonPipeline(person_id=person_id, stage="Replied", version=0)
            session.add(card)
        if card.version != version or (
            stage != card.stage and stage not in TRANSITIONS[card.stage]
        ):
            raise ValueError("Stage changed or transition is invalid; reload the card")
        if next_due and (next_due.tzinfo is None or next_due <= utc_now()):
            raise ValueError("Choose a future Calgary follow-up time")
        card.stage, card.note, card.next_due = stage, note.strip(), next_due
        card.version, card.updated_at = card.version + 1, utc_now()
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.pipeline_updated",
                entity_id=person_id,
                details={"stage": stage},
            )
        )
