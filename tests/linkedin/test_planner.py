"""Daily plans remain bounded, attributable and safe after a machine restart."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.lead_models import Campaign
from app.db.linkedin_models import LinkedInTask, PersonOwnership
from app.db.linkedin_queue_models import (
    LinkedInQuota,
    LinkedInSequence,
)
from app.db.session import Database
from app.linkedin.campaigns import ownership_active, save_routing
from app.linkedin.identity import add_profile, assign_person
from app.linkedin.planner import plan_day
from app.linkedin.quotas import Quotas, save_quotas
from app.sending.window import CALGARY
from tests.acquisition.helpers import business
from tests.conftest import Account
from tests.linkedin.test_identity import person

NOW = datetime(2026, 11, 9, 7, 30, tzinfo=CALGARY).astimezone(UTC)


def seed(db: Database, owner: Account) -> tuple[str, str]:
    """Assign one synthetic decision maker without creating any message approval."""
    company, _ = business(db, owner)
    member = add_profile(db, owner.user.id, "https://linkedin.com/in/member-test", owner.user.id)
    target = person(db, company.id, owner, email="alex@example.com")
    assign_person(db, target.id, member.id, owner.user.id)
    return member.id, target.id


def test_morning_plan_is_idempotent_and_never_approves(db: Database, owner: Account) -> None:
    """Repeated worker ticks create one reservation and one unsendable human task."""
    profile, _ = seed(db, owner)
    assert plan_day(db, NOW - timedelta(minutes=1)) == 0
    assert plan_day(db, NOW) == 1
    assert plan_day(db, NOW) == 0
    with db.sessions() as session:
        tasks = list(session.scalars(select(LinkedInTask)))
        assert len(tasks) == 1 and tasks[0].action == "invite"
        assert tasks[0].release_at == NOW + timedelta(minutes=90)
        assert tasks[0].state == "pending" and tasks[0].approval_digest is None
        assert len(list(session.scalars(select(LinkedInQuota)))) == 1
    save_quotas(db, profile, Quotas(invites=0), owner.user.id)
    assert plan_day(db, NOW + timedelta(days=1)) == 0


def test_linkedin_industry_routes_a_confirmed_person_independently_of_email(
    db: Database, owner: Account
) -> None:
    """Enable and pause LinkedIn work while the email campaign stays paused."""
    company, campaign = business(db, owner)
    profile = add_profile(
        db, owner.user.id, "https://linkedin.com/in/industry-member", owner.user.id
    )
    target = person(db, company.id, owner)
    with db.sessions.begin() as session:
        current = session.get(Campaign, campaign.id)
        assert current
        current.active = False
    save_routing(db, campaign.id, profile.id, True, 0, owner.user.id)
    assert plan_day(db, NOW) == 1
    assert plan_day(db, NOW) == 0
    with db.sessions() as session:
        current = session.get(Campaign, campaign.id)
        ownership = session.get(PersonOwnership, target.id)
        assert current and not current.active and ownership and ownership.profile_id == profile.id
        assert ownership_active(session, ownership)
        task = session.scalar(select(LinkedInTask))
        assert task and task.profile_id == profile.id and not task.approval_digest
    save_routing(db, campaign.id, profile.id, False, 1, owner.user.id)
    with db.sessions() as session:
        ownership = session.get(PersonOwnership, target.id)
        assert ownership and not ownership_active(session, ownership)
    assert plan_day(db, NOW + timedelta(days=1)) == 0


def test_late_restart_spreads_tasks_and_does_not_double_reserve(
    db: Database, owner: Account
) -> None:
    """Missed morning work moves to future slots, not an immediate catch-up burst."""
    seed(db, owner)
    plan_day(db, NOW)
    restart = NOW + timedelta(hours=5)
    plan_day(db, restart)
    with db.sessions() as session:
        task = session.scalar(select(LinkedInTask))
        assert task and task.release_at and task.release_at > restart
        release = task.release_at
        assert len(list(session.scalars(select(LinkedInQuota)))) == 1
    plan_day(db, restart + timedelta(seconds=10))
    with db.sessions() as session:
        task = session.scalar(select(LinkedInTask))
        assert task and task.release_at == release


def test_stale_followup_requires_review_and_weekend_never_plans(
    db: Database, owner: Account
) -> None:
    """More than five days late never becomes a new cold task automatically."""
    profile, target = seed(db, owner)
    with db.sessions.begin() as session:
        session.add(
            LinkedInSequence(
                person_id=target,
                profile_id=profile,
                generation=1,
                state="accepted",
                next_action="message1",
                next_due=NOW - timedelta(days=6),
            )
        )
    assert plan_day(db, NOW) == 0
    with db.sessions() as session:
        sequence = session.get(LinkedInSequence, target)
        assert sequence and sequence.state == "review"
    assert plan_day(db, NOW + timedelta(days=5)) == 0


def test_expired_human_claim_is_held_never_replayed(db: Database, owner: Account) -> None:
    """A shutdown after the member starts an action cannot cause an automatic retry."""
    seed(db, owner)
    plan_day(db, NOW)
    with db.sessions.begin() as session:
        task = session.scalar(select(LinkedInTask))
        assert task
        task.state, task.lease_token = "acting", "synthetic-claim"
        task.lease_expires_at = NOW + timedelta(minutes=5)
    plan_day(db, NOW + timedelta(hours=2))
    with db.sessions() as session:
        task = session.scalar(select(LinkedInTask))
        assert task and task.state == "needs_check"
        assert task.lease_token == "synthetic-claim"


def test_pending_cap_and_old_invitation_withdrawal(db: Database, owner: Account) -> None:
    """A full pending-invitation allowance blocks new invites but still plans human withdrawal."""
    from app.db.linkedin_models import Person
    from tests.linkedin.test_identity import person

    profile, target = seed(db, owner)
    with db.sessions() as session:
        existing = session.get(Person, target)
        assert existing
        company_id = existing.company_id
    second = person(db, company_id, owner, "Second Example", url="https://linkedin.com/in/second")
    assign_person(db, second.id, profile, owner.user.id)
    save_quotas(db, profile, Quotas(pending=1), owner.user.id)
    with db.sessions.begin() as session:
        session.add(
            LinkedInSequence(
                person_id=target,
                profile_id=profile,
                generation=1,
                state="invited",
                next_action="invite",
                next_due=NOW,
                invited_at=NOW - timedelta(days=22),
            )
        )
    assert plan_day(db, NOW) == 1
    with db.sessions() as session:
        tasks = list(
            session.scalars(select(LinkedInTask).where(LinkedInTask.release_at.is_not(None)))
        )
        assert len(tasks) == 1 and tasks[0].action == "withdraw"
