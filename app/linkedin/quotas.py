"""Portable serialized profile capacity with immutable policy ceilings."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.linkedin_models import LinkedInProfile, LinkedInTask
from app.db.linkedin_queue_models import (
    LinkedInPolicy,
    LinkedInQuota,
    LinkedInSequence,
)
from app.db.models import AuditEvent, WorkspaceSetting
from app.db.session import Database
from app.linkedin.identity import require_staff
from app.linkedin.timing import local_day, week_start
from app.sending.window import CALGARY


class Quotas(BaseModel):
    """Owner may lower limits; hard ceilings cannot be raised by settings."""

    model_config = ConfigDict(extra="forbid")
    invites: int = Field(default=20, ge=0, le=20)
    invites_week: int = Field(default=80, ge=0, le=80)
    pending: int = Field(default=300, ge=0, le=300)
    first_messages: int = Field(default=15, ge=0, le=15)
    all_messages: int = Field(default=30, ge=0, le=30)
    views: int = Field(default=40, ge=0, le=40)
    comments: int = Field(default=10, ge=0, le=10)
    posts_week: int = Field(default=4, ge=0, le=4)
    posts_day: int = Field(default=1, ge=0, le=1)
    inmail: int = Field(default=0, ge=0, le=0)


def lock_registry(session: Session) -> None:
    """Serialize the ten-member registration cap with a durable singleton write lock."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    session.execute(
        insert(WorkspaceSetting)
        .values(key="linkedin_registry", value={}, version=0, updated_at=utc_now())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    changed = session.scalar(
        update(WorkspaceSetting)
        .where(WorkspaceSetting.key == "linkedin_registry")
        .values(version=WorkspaceSetting.version + 1)
        .returning(WorkspaceSetting.key)
    )
    if changed is None:
        raise ValueError("Apply the latest database migration first")


def lock_policy(session: Session, profile_id: str, now: datetime) -> LinkedInPolicy:
    """Acquire a profile write lock before reading any daily or weekly count."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    session.execute(
        insert(LinkedInPolicy)
        .values(profile_id=profile_id, limits={}, revision=0, started_at=now)
        .on_conflict_do_nothing(index_elements=["profile_id"])
    )
    session.execute(
        update(LinkedInPolicy)
        .where(LinkedInPolicy.profile_id == profile_id)
        .values(revision=LinkedInPolicy.revision + 1)
    )
    policy = session.get(LinkedInPolicy, profile_id, populate_existing=True)
    assert policy
    return policy


def limits(policy: LinkedInPolicy, now: datetime) -> Quotas:
    """Apply conservative ceilings through the first fourteen Calgary calendar days."""
    value = Quotas.model_validate(policy.limits)
    if (now.astimezone(CALGARY).date() - policy.started_at.astimezone(CALGARY).date()).days < 14:
        value.invites = min(value.invites, 10)
        value.first_messages = min(value.first_messages, 5)
        value.views = min(value.views, 20)
        value.comments = min(value.comments, 5)
        value.posts_week = min(value.posts_week, 2)
    return value


def save_quotas(db: Database, profile_id: str, value: Quotas, actor: str) -> None:
    """Record owner policy changes, preserving already consumed reservations."""
    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        if not session.get(LinkedInProfile, profile_id):
            raise ValueError("Member profile not found")
        policy = lock_policy(session, profile_id, utc_now())
        policy.limits = value.model_dump()
        session.add(
            AuditEvent(actor_id=actor, action="linkedin.quotas_changed", entity_id=profile_id)
        )


def reserve_quota(session: Session, task: LinkedInTask, now: datetime) -> bool:
    """Reserve once per task/day; cancellations conservatively do not refund capacity."""
    policy = lock_policy(session, task.profile_id, now)
    cap, day = limits(policy, now), local_day(now)
    old = session.scalar(
        select(LinkedInQuota.id).where(
            LinkedInQuota.task_id == task.id, LinkedInQuota.local_day == day
        )
    )
    counts: dict[str, int] = {}
    for action in session.scalars(
        select(LinkedInQuota.action).where(
            LinkedInQuota.profile_id == task.profile_id, LinkedInQuota.local_day == day
        )
    ):
        counts[action] = counts.get(action, 0) + 1
    action = task.action
    if old:
        counts[action] = max(0, counts.get(action, 0) - 1)
    daily = {
        "invite": cap.invites,
        "message1": cap.first_messages,
        "view": cap.views,
        "comment": cap.comments,
        "post": cap.posts_day,
        "withdraw": 20,
        "inmail": 0,
    }
    if action not in {*daily, "message2", "message3", "reply"}:
        return False
    if action in daily and counts.get(action, 0) >= daily[action]:
        return False
    if (
        action in {"message1", "message2", "message3", "reply"}
        and sum(counts.get(key, 0) for key in ("message1", "message2", "message3", "reply"))
        >= cap.all_messages
    ):
        return False
    # Each profile-opening task reserves a view too; manual work outside this UI is
    # not observable. Count plans conservatively rather than overpromise platform usage.
    profile_actions = {"view", "invite", "message1", "message2", "message3", "reply", "comment"}
    if (
        action in profile_actions
        and sum(counts.get(key, 0) for key in profile_actions) >= cap.views
    ):
        return False
    if action in {"invite", "post"}:
        weekly = (
            session.scalar(
                select(func.count())
                .select_from(LinkedInQuota)
                .where(
                    LinkedInQuota.profile_id == task.profile_id,
                    LinkedInQuota.action == action,
                    LinkedInQuota.local_day >= week_start(now),
                    LinkedInQuota.local_day <= day,
                )
            )
            or 0
        )
        if weekly - bool(old) >= (cap.invites_week if action == "invite" else cap.posts_week):
            return False
    if action == "invite":
        # Include prepared invitations so the pending cap cannot be exceeded by a batch.
        pending_people = set(
            session.scalars(
                select(LinkedInSequence.person_id).where(
                    LinkedInSequence.profile_id == task.profile_id,
                    LinkedInSequence.state == "invited",
                )
            )
        )
        pending_people.update(
            session.scalars(
                select(LinkedInTask.person_id).where(
                    LinkedInTask.profile_id == task.profile_id,
                    LinkedInTask.action == "invite",
                    LinkedInTask.id != task.id,
                    LinkedInTask.planned_day.is_not(None),
                    LinkedInTask.state.not_in(["done", "cancelled"]),
                )
            )
        )
        if len(pending_people) >= cap.pending:
            return False
    if not old:
        session.add(
            LinkedInQuota(
                task_id=task.id,
                profile_id=task.profile_id,
                local_day=day,
                action=action,
                created_at=now,
            )
        )
        session.flush()
    return True
