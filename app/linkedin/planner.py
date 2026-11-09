"""Idempotent human-only daily plans with bounded catch-up and conservative recovery."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.linkedin_models import LinkedInProfile, LinkedInTask, Person, PersonOwnership
from app.db.linkedin_queue_models import LinkedInSequence
from app.db.models import User
from app.db.session import Database
from app.linkedin.coordination import outreach_block
from app.linkedin.quotas import lock_policy, reserve_quota
from app.linkedin.timing import daytime, local_day, slots
from app.operations.control import maintenance_enabled


def plan_profile(db: Database, profile_id: str, now: datetime) -> int:
    """Prepare one member's remaining day; never open a site, send or approve text."""
    with db.sessions.begin() as session:
        if maintenance_enabled(session):
            return 0
        policy = lock_policy(session, profile_id, now)
        profile = session.get(LinkedInProfile, profile_id, populate_existing=True)
        user = session.get(User, profile.user_id) if profile else None
        if not profile or profile.status != "active" or not user or not user.active:
            return 0
        today = local_day(now)
        # A human may have acted outside this app before a shutdown. Never replay that claim.
        for expired in session.scalars(
            select(LinkedInTask).where(
                LinkedInTask.profile_id == profile_id,
                LinkedInTask.state == "acting",
                LinkedInTask.lease_expires_at <= now,
            )
        ):
            expired.state, expired.reason = (
                "needs_check",
                "Action result unknown after lease expiry",
            )
        pairs = list(
            session.execute(
                select(Person, PersonOwnership)
                .join(PersonOwnership, PersonOwnership.person_id == Person.id)
                .where(PersonOwnership.profile_id == profile_id, PersonOwnership.active.is_(True))
            )
        )
        due: list[LinkedInTask] = []
        for person, ownership in pairs:
            if outreach_block(session, person.company_id) or person.presence in {"low", "none"}:
                continue
            sequence = session.get(LinkedInSequence, person.id)
            if sequence and sequence.profile_id != profile_id:
                if sequence.hold_until and sequence.hold_until > now:
                    continue
                sequence.profile_id = profile_id
                sequence.generation += 1
                sequence.state, sequence.next_action = "assigned", "invite"
                sequence.next_due = now
            if sequence is None:
                sequence = LinkedInSequence(
                    person_id=person.id,
                    profile_id=profile_id,
                    generation=1,
                    state="accepted" if ownership.warm else "assigned",
                    next_action="message1" if ownership.warm else "invite",
                    next_due=now + timedelta(days=2) if ownership.warm else now,
                    accepted_at=now if ownership.warm else None,
                )
                session.add(sequence)
                session.flush()
            if sequence.state in {"review", "closed", "withdrawn", "replied"}:
                continue
            if sequence.state == "invited":
                if not sequence.invited_at or now < sequence.invited_at + timedelta(days=21):
                    continue
                sequence.next_action = "withdraw"
                sequence.next_due = sequence.invited_at + timedelta(days=21)
            if sequence.next_due > now:
                continue
            key = f"linkedin:{person.id}:{sequence.generation}:{sequence.next_action}"
            task = session.scalar(select(LinkedInTask).where(LinkedInTask.key == key))
            if sequence.next_action.startswith("message") and now - sequence.next_due > timedelta(
                days=5
            ):
                sequence.state = "review"
                if task and task.state in {"pending", "ready"}:
                    task.state, task.reason = "review", "Follow-up more than five days late"
                continue
            if task is None:
                task = LinkedInTask(
                    key=key,
                    company_id=person.company_id,
                    person_id=person.id,
                    profile_id=profile_id,
                    action=sequence.next_action,
                    generation=sequence.generation,
                    due_at=sequence.next_due,
                )
                session.add(task)
                session.flush()
            if task.state not in {"pending", "ready"}:
                continue
            # A still-future slot is stable across ordinary ticks. A missed slot gets spread.
            if task.planned_day == today and task.release_at and task.release_at >= now:
                continue
            if task.planned_day == today and task.state == "ready":
                continue  # Active human queue is not endlessly pushed into the future.
            due.append(task)
        due.sort(
            key=lambda task: (0 if task.action.startswith("message") else 1, task.due_at, task.id)
        )
        allocated: list[LinkedInTask] = []
        available = slots(now, len(due))
        for task in due:
            if len(allocated) >= len(available):
                break
            if reserve_quota(session, task, now):
                allocated.append(task)
        for task, release in zip(allocated, slots(now, len(allocated)), strict=True):
            task.planned_day, task.release_at, task.reason = (
                today,
                release,
                "Owner approval required",
            )
            # Only L3 can make an exact-approved task ready. Planning cannot confer approval.
            task.state = "pending"
        policy.planned_day = today
        return len(allocated)


def plan_day(db: Database, now: datetime) -> int:
    """Run from the dedicated worker after 07:30, including a late weekday startup."""
    if not daytime(now, planning=True):
        return 0
    with db.sessions() as session:
        profiles = list(session.scalars(select(LinkedInProfile.id)))
    return sum(plan_profile(db, profile, now) for profile in profiles)
