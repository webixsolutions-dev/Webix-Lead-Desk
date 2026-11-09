"""Record explicit human outcomes; no browser automation or LinkedIn messaging API."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, PersonOwnership
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.models import AuditEvent
from app.db.session import Database
from app.linkedin.coordination import lock_company, record_touch
from app.linkedin.drafts import latest, valid
from app.linkedin.identity import require_staff
from app.linkedin.leases import live_claim
from app.linkedin.quotas import lock_policy


def member_task(session: Session, task_id: str, actor: str) -> LinkedInTask:
    """Restrict human execution to the task's own real member, including owners."""
    require_staff(session, actor)
    task = session.get(LinkedInTask, task_id)
    profile = session.get(LinkedInProfile, task.profile_id) if task else None
    if not task or not profile or profile.user_id != actor:
        raise ValueError("This is another member's task")
    lock_company(session, task.company_id)
    lock_policy(session, task.profile_id, utc_now())
    session.refresh(task)
    return task


def complete(db: Database, task_id: str, actor: str, token: str, now: datetime) -> None:
    """Record a confirmed manual send once, requiring the still-valid action lease."""
    with db.sessions.begin() as session:
        task = member_task(session, task_id, actor)
        if task.state == "done" and task.lease_token == token:
            return
        if not live_claim(task, token, now) or not valid(session, task, now):
            raise ValueError("Action expired or changed; check the actual result before continuing")
        finish(session, task, actor, now)


def finish(
    session: Session, task: LinkedInTask, actor: str, now: datetime, *, advance: bool = True
) -> None:
    """Persist actual action evidence before progressing the human-only sequence."""
    draft = latest(session, task.id)
    if not draft:
        raise ValueError("The exact action record is missing")
    task.state, task.completed_at, task.reason = "done", now, "Human confirmed completion"
    record_touch(
        session,
        task.company_id,
        "linkedin_sent:" + task.id,
        "linkedin",
        "outbound",
        task.action,
        person_id=task.person_id,
        profile_id=task.profile_id,
        actor=actor,
        text=draft.snapshot["body"]
        + ("\n\n" + draft.snapshot["footer"] if draft.snapshot["footer"] else ""),
        at=now,
    )
    seq = session.get(LinkedInSequence, task.person_id)
    if not advance or not seq or task.action == "reply" or seq.generation != task.generation:
        return
    ownership = session.get(PersonOwnership, task.person_id)
    if task.action == "invite":
        seq.state, seq.invited_at = "invited", now
    elif task.action in {"message1", "message2"}:
        seq.state = "messaging"
        seq.next_action = "message2" if task.action == "message1" else "message3"
        seq.next_due = now + timedelta(days=5 if task.action == "message1" else 8)
    elif task.action in {"message3", "withdraw"}:
        seq.state = "closed" if task.action == "message3" else "withdrawn"
        seq.hold_until = now + timedelta(days=90 if task.action == "message3" else 21)
        if task.action == "withdraw":
            seq.withdrawn_at = now
        if ownership:
            ownership.active, ownership.locked_until = False, now + timedelta(days=180)
        # The second assigned person may still finish their already reviewed sequence.
        others = session.scalar(
            select(PersonOwnership.person_id)
            .join(Person, Person.id == PersonOwnership.person_id)
            .where(
                Person.company_id == task.company_id,
                PersonOwnership.person_id != task.person_id,
                PersonOwnership.active.is_(True),
            )
            .limit(1)
        )
        if not others and task.action == "message3":
            company = lock_company(session, task.company_id)
            if company.state == "active":
                company.state, company.reason, company.hold_until = (
                    "cooldown",
                    "Sequence complete without a reply",
                    now + timedelta(days=90),
                )


def resolve(db: Database, task_id: str, actor: str, outcome: str, note: str, now: datetime) -> None:
    """Owner resolves an ambiguous manual action; absence of proof never means retry."""
    if outcome not in {"sent", "not_sent"} or not 10 <= len(note.strip()) <= 1000:
        raise ValueError("Record the checked LinkedIn result and a factual note")
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        task = session.get(LinkedInTask, task_id)
        if not task:
            raise ValueError("Task not found")
        lock_company(session, task.company_id)
        session.refresh(task)
        if task.state not in {"needs_check", "acting", "cancelled"} or not task.lease_token:
            raise ValueError("Only a previously claimed action can be reconciled")
        if outcome == "sent":
            # Record an actual outcome even if a reply arrived after the action started.
            # Never restart a cancelled sequence in that case.
            from app.linkedin.coordination import outreach_block

            blocked = outreach_block(session, task.company_id)
            seq = session.get(LinkedInSequence, task.person_id)
            if blocked and seq:
                seq.state = "replied"
            finish(session, task, actor, now, advance=not bool(blocked))
            if blocked and seq:
                seq.state = "replied"
        else:
            task.state, task.approval_digest, task.lease_token = "pending", None, None
            task.release_at, task.planned_day = None, None
            draft = latest(session, task.id)
            if draft:
                draft.state = "superseded"
        session.add(
            AuditEvent(
                actor_id=actor,
                action="linkedin.action_reconciled",
                entity_id=task.id,
                details={"outcome": outcome, "note": note.strip()},
            )
        )


def person_event(db: Database, person_id: str, actor: str, event: str, now: datetime) -> None:
    """Acceptances and wrong-person findings are human observations, never inferred replies."""
    if event not in {"accepted", "not_on_linkedin", "wrong_person"}:
        raise ValueError("Choose an observed outcome")
    with db.sessions.begin() as session:
        user = require_staff(session, actor)
        person = session.get(Person, person_id)
        ownership = session.get(PersonOwnership, person_id)
        profile = session.get(LinkedInProfile, ownership.profile_id) if ownership else None
        if (
            not person
            or not ownership
            or not profile
            or (user.role != "owner" and profile.user_id != actor)
        ):
            raise ValueError("This person belongs to another member")
        lock_company(session, person.company_id)
        seq = session.get(LinkedInSequence, person_id)
        if not seq:
            raise ValueError("Plan this person before recording a sequence outcome")
        if event == "accepted":
            from app.linkedin.coordination import outreach_block

            if seq.state == "accepted":
                return
            if seq.state != "invited" or outreach_block(session, person.company_id):
                raise ValueError("Only a pending invitation can become an accepted connection")
            seq.state, seq.accepted_at, seq.next_action = "accepted", now, "message1"
            seq.next_due = now + timedelta(days=2)
        else:
            person.presence = "none" if event == "not_on_linkedin" else "unknown"
            ownership.active, ownership.locked_until = False, now + timedelta(days=180)
            seq.state = "closed"
        for task in session.scalars(
            select(LinkedInTask).where(
                LinkedInTask.person_id == person_id,
                LinkedInTask.state.not_in(["done", "cancelled"]),
            )
        ):
            task.state, task.reason = "cancelled", "Human observation: " + event
        record_touch(
            session,
            person.company_id,
            f"linkedin_event:{person_id}:{seq.generation}:{event}",
            "linkedin",
            "observation",
            event,
            person_id=person.id,
            profile_id=profile.id,
            actor=actor,
            at=now,
        )
