"""Fenced human task starts; there is no automated LinkedIn action executor."""

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.linkedin_models import LinkedInProfile, LinkedInTask, PersonOwnership
from app.db.models import new_id
from app.db.session import Database
from app.linkedin.coordination import lock_company, outreach_block
from app.linkedin.day_fence import reserve_touch
from app.linkedin.identity import require_staff
from app.linkedin.quotas import lock_policy, reserve_quota
from app.linkedin.timing import daytime, local_day
from app.operations.control import maintenance_enabled


def claim_task(
    db: Database,
    task_id: str,
    actor: str,
    now: datetime,
    *,
    approved: Callable[[Session, LinkedInTask], bool],
) -> str | None:
    """Reserve one member's exact-approved task before exposing its human action button."""
    if not daytime(now):
        return None
    with db.sessions.begin() as session:
        require_staff(session, actor)
        task = session.get(LinkedInTask, task_id)
        if not task or maintenance_enabled(session):
            return None
        lock_company(session, task.company_id)
        lock_policy(session, task.profile_id, now)
        session.refresh(task)
        from app.linkedin.learning import evaluate_pause

        if evaluate_pause(session, task.profile_id, now):
            return None
        profile = session.get(LinkedInProfile, task.profile_id)
        ownership = session.get(PersonOwnership, task.person_id)
        if (
            not profile
            or profile.user_id != actor
            or profile.status != "active"
            or not ownership
            or (not ownership.active and task.action != "reply")
            or ownership.profile_id != profile.id
            or task.state != "ready"
            or task.planned_day != local_day(now)
            or not task.release_at
            or task.release_at > now
            or task.due_at > now
            or not task.approval_digest
            or not approved(session, task)
            or outreach_block(session, task.company_id, response=task.action == "reply")
        ):
            return None
        if not reserve_quota(session, task, now):
            return None
        if task.action not in {"view", "withdraw"} and not reserve_touch(
            session, task.person_id, "linkedin:" + task.id, "linkedin", now
        ):
            task.reason = "A different channel already reserved this person today"
            return None
        task.state, task.lease_token = "acting", new_id()
        task.lease_expires_at = now + timedelta(minutes=15)
        return task.lease_token


def live_claim(task: LinkedInTask, token: str, now: datetime) -> bool:
    """Check a completion fence without accepting an expired or replaced browser action."""
    return bool(
        task.state == "acting"
        and task.lease_token == token
        and task.lease_expires_at
        and task.lease_expires_at > now
    )
