"""Owner decisions for stale sequences, separately requested replies and voice guidance."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, PersonOwnership, Touch
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.models import AuditEvent, new_id
from app.db.session import Database
from app.linkedin.coordination import lock_company, outreach_block
from app.linkedin.identity import require_staff
from app.linkedin.quotas import lock_policy, reserve_quota
from app.linkedin.timing import local_day, slots


def reply_task(db: Database, person_id: str, actor: str, now: datetime) -> LinkedInTask:
    """Create a separately reviewable reply to the latest real inbound LinkedIn message."""
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        person = session.get(Person, person_id)
        ownership = session.get(PersonOwnership, person_id)
        if not person or not ownership:
            raise ValueError("Assign a real member before preparing a reply")
        lock_company(session, person.company_id)
        blocked = outreach_block(session, person.company_id, response=True)
        incoming = session.scalar(
            select(Touch)
            .where(
                Touch.person_id == person_id,
                Touch.direction == "inbound",
                Touch.channel == "linkedin",
            )
            .order_by(Touch.occurred_at.desc())
            .limit(1)
        )
        if blocked or not incoming:
            raise ValueError(blocked or "Record the actual incoming LinkedIn reply first")
        key = "linkedin_reply:" + incoming.id
        existing = session.scalar(select(LinkedInTask).where(LinkedInTask.key == key))
        if existing:
            return existing
        task = LinkedInTask(
            key=key,
            person_id=person_id,
            company_id=person.company_id,
            profile_id=ownership.profile_id,
            action="reply",
            due_at=now,
            generation=0,
        )
        session.add(task)
        session.flush()
        return task


def schedule_replies(db: Database, profile_id: str, now: datetime) -> int:
    """Release approved requested responses within the same bounded member capacities."""
    from app.linkedin.drafts import valid

    with db.sessions.begin() as session:
        lock_policy(session, profile_id, now)
        tasks = list(
            session.scalars(
                select(LinkedInTask)
                .where(
                    LinkedInTask.profile_id == profile_id,
                    LinkedInTask.action == "reply",
                    LinkedInTask.state.in_(["pending", "ready"]),
                )
                .order_by(LinkedInTask.due_at)
            )
        )
        pending = [t for t in tasks if t.planned_day != local_day(now) or not t.release_at]
        count = 0
        for task, release in zip(pending, slots(now, len(pending)), strict=False):
            if valid(session, task, now) and reserve_quota(session, task, now):
                task.planned_day, task.release_at, task.state = local_day(now), release, "ready"
                count += 1
        return count


def review_sequence(
    db: Database, person_id: str, actor: str, decision: str, note: str, now: datetime
) -> None:
    """Review overdue work or a new angle after silence; never revive uncertain sends."""
    if decision not in {"cancel", "reschedule", "restart"} or not 20 <= len(note.strip()) <= 1000:
        raise ValueError("Choose a decision and record the review or new angle")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        person = session.get(Person, person_id)
        seq = session.get(LinkedInSequence, person_id)
        own = session.get(PersonOwnership, person_id)
        if not person or not seq or not own:
            raise ValueError("Sequence not found")
        company = lock_company(session, person.company_id)
        if session.scalar(
            select(LinkedInTask.id)
            .where(
                LinkedInTask.person_id == person_id,
                LinkedInTask.state.in_(["acting", "needs_check"]),
            )
            .limit(1)
        ):
            raise ValueError("Reconcile the unknown action before changing its sequence")
        if decision == "restart":
            if (
                seq.state not in {"closed", "withdrawn", "replied"}
                or not seq.hold_until
                or seq.hold_until > now
            ):
                raise ValueError("The person is still in cooldown")
            if company.state == "cooldown" and company.hold_until and company.hold_until <= now:
                company.state, company.reason, company.hold_until = "active", "", None
            if outreach_block(session, person.company_id):
                raise ValueError("Company outreach is still blocked")
            if company.profile_id != own.profile_id or seq.profile_id != own.profile_id:
                raise ValueError("Review the current member assignment first")
            other_count = len(
                list(
                    session.scalars(
                        select(PersonOwnership.person_id)
                        .join(Person)
                        .where(
                            Person.company_id == person.company_id,
                            PersonOwnership.active.is_(True),
                            PersonOwnership.person_id != person_id,
                        )
                    )
                )
            )
            if other_count >= 2:
                raise ValueError("Two people are already active at this business")
            seq.state, seq.next_action = "assigned", "invite" if not seq.accepted_at else "message1"
            seq.generation += 1
            seq.next_due, seq.hold_until, own.active = now, None, True
        elif decision == "reschedule":
            if seq.state != "review" or outreach_block(session, person.company_id):
                raise ValueError("Only an eligible overdue follow-up can be rescheduled")
            seq.state, seq.next_due = "messaging", now
            seq.generation += 1  # Fresh exact copy approval is required.
        else:
            seq.state, own.active = "closed", False
            from datetime import timedelta

            seq.hold_until = now + timedelta(days=90)
            own.locked_until = now + timedelta(days=180)
        for task in session.scalars(
            select(LinkedInTask).where(
                LinkedInTask.person_id == person_id,
                LinkedInTask.state.in_(["pending", "ready", "review"]),
            )
        ):
            task.state, task.reason = "cancelled", "Owner sequence review"
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.sequence_reviewed",
                entity_id=person_id,
                details={"decision": decision, "note": note.strip(), "key": new_id()},
            )
        )


def save_voice(db: Database, profile_id: str, actor: str, values: dict[str, Any]) -> None:
    """Persist explicit owner guidance; scraped text cannot change a member voice card."""
    keys = {"role", "opinions", "words_to_use", "words_to_avoid", "approved_stories"}
    if set(values) != keys or any(not isinstance(v, str) or len(v) > 3000 for v in values.values()):
        raise ValueError("Use the five bounded voice-card fields")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        profile = session.get(LinkedInProfile, profile_id)
        if not profile:
            raise ValueError("Member not found")
        profile.voice_card = values
        session.add(
            AuditEvent(actor_id=actor, action="linkedin.voice_reviewed", entity_id=profile_id)
        )
