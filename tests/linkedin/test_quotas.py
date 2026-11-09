"""Real quota boundaries and independent concurrent reservation transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.linkedin_models import LinkedInTask, Person
from app.db.linkedin_queue_models import LinkedInQuota
from app.db.session import Database
from app.linkedin.day_fence import reserve_touch
from app.linkedin.leases import claim_task, live_claim
from app.linkedin.planner import plan_day
from app.linkedin.quotas import Quotas, limits, lock_policy, reserve_quota, save_quotas
from app.linkedin.timing import daytime, slots
from tests.conftest import Account
from tests.linkedin.test_planner import NOW, seed


def task(db: Database, profile: str, person_id: str, index: int, action: str) -> str:
    """Prepare distinct synthetic actions for testing capacity without sending anything."""
    with db.sessions.begin() as session:
        target = session.get(Person, person_id)
        assert target
        item = LinkedInTask(
            key=f"test:{index}",
            profile_id=profile,
            person_id=person_id,
            company_id=target.company_id,
            action=action,
            due_at=NOW,
        )
        session.add(item)
        session.flush()
        return item.id


def test_ramp_and_immutable_ceilings(db: Database, owner: Account) -> None:
    """Warmup lasts fourteen local days; settings cannot bypass hard maxima or InMail zero."""
    profile, _ = seed(db, owner)
    with db.sessions.begin() as session:
        policy = lock_policy(session, profile, NOW)
        assert limits(policy, NOW + timedelta(days=13)).invites == 10
        assert limits(policy, NOW + timedelta(days=14)).invites == 20
        assert limits(policy, NOW).posts_week == 2
    for value in ({"invites": 21}, {"invites_week": 81}, {"inmail": 1}, {"all_messages": 31}):
        with pytest.raises(ValueError):
            Quotas.model_validate(value)


@pytest.mark.parametrize(
    ("action", "maximum"),
    [
        ("invite", 10),
        ("message1", 5),
        ("message2", 20),
        ("view", 20),
        ("comment", 5),
        ("post", 1),
        ("inmail", 0),
    ],
)
def test_daily_quota_cannot_overflow(
    db: Database, owner: Account, action: str, maximum: int
) -> None:
    """Every action family reserves bounded capacity before becoming actionable."""
    profile, target = seed(db, owner)
    for index in range(maximum + 1):
        task_id = task(db, profile, target, index, action)
        with db.sessions.begin() as session:
            current = session.get(LinkedInTask, task_id)
            assert current
            assert reserve_quota(session, current, NOW) is (index < maximum)


def test_weekly_reservations_and_lowered_limits(db: Database, owner: Account) -> None:
    """Restart does not reset consumed weekly slots; lowering a cap blocks existing tasks."""
    profile, target = seed(db, owner)
    save_quotas(db, profile, Quotas(invites_week=1), owner.user.id)
    first = task(db, profile, target, 0, "invite")
    second = task(db, profile, target, 1, "invite")
    with db.sessions.begin() as session:
        current = session.get(LinkedInTask, first)
        assert current and reserve_quota(session, current, NOW)
        assert reserve_quota(session, current, NOW)
    with db.sessions.begin() as session:
        current = session.get(LinkedInTask, second)
        assert current and not reserve_quota(session, current, NOW + timedelta(days=1))
    save_quotas(db, profile, Quotas(invites=0), owner.user.id)
    with db.sessions.begin() as session:
        current = session.get(LinkedInTask, first)
        assert current and not reserve_quota(session, current, NOW)
        assert len(list(session.scalars(select(LinkedInQuota)))) == 1


def test_concurrent_last_slot_only_has_one_winner(db: Database, owner: Account) -> None:
    """Separate real transactions contend on the profile lock, not an in-process counter."""
    profile, target = seed(db, owner)
    save_quotas(db, profile, Quotas(invites=1), owner.user.id)
    ids = [task(db, profile, target, index, "invite") for index in range(2)]

    def reserve(item_id: str) -> bool:
        """Try to reserve the last available invitation."""
        with db.sessions.begin() as session:
            current = session.get(LinkedInTask, item_id)
            assert current
            return reserve_quota(session, current, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ids)) == [False, True]


def test_email_and_linkedin_contend_for_same_person_day(db: Database, owner: Account) -> None:
    """Even independent channels cannot both reserve a named person on the same day."""
    _, target = seed(db, owner)

    def reserve(channel: str) -> bool:
        """Reserve from independent channel transactions."""
        with db.sessions.begin() as session:
            return reserve_touch(session, target, channel + ":test", channel, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ["email", "linkedin"])) == [False, True]
    with db.sessions.begin() as session:
        assert reserve_touch(session, target, "new:test", "email", NOW + timedelta(days=1))


def test_claim_requires_current_approval_owner_window_and_fence(
    db: Database, owner: Account, reviewer: Account
) -> None:
    """Nobody can start another member's task or resume an already claimed task."""
    seed(db, owner)
    plan_day(db, NOW)
    with db.sessions.begin() as session:
        item = session.scalar(select(LinkedInTask))
        assert item
        item.state, item.approval_digest = "ready", "synthetic-approved-digest"
        item_id = item.id
    later = NOW + timedelta(hours=2)
    assert claim_task(db, item_id, owner.user.id, later, approved=lambda s, t: False) is None
    assert claim_task(db, item_id, reviewer.user.id, later, approved=lambda s, t: True) is None
    assert claim_task(db, item_id, owner.user.id, NOW, approved=lambda s, t: True) is None
    token = claim_task(db, item_id, owner.user.id, later, approved=lambda s, t: True)
    assert token
    assert claim_task(db, item_id, owner.user.id, later, approved=lambda s, t: True) is None
    with db.sessions() as session:
        item = session.get(LinkedInTask, item_id)
        assert item and live_claim(item, token, later)
        assert not live_claim(item, "wrong-token", later)
        assert not live_claim(item, token, later + timedelta(minutes=16))


def test_dst_weekends_and_late_afternoon_are_bounded() -> None:
    """Local schedule survives DST and never pushes catch-up outside the workday."""
    from datetime import UTC, datetime

    assert slots(datetime(2026, 7, 6, 13, 30, tzinfo=UTC), 1)[0].hour == 15
    assert slots(NOW, 1)[0].hour == 15
    assert slots(datetime(2025, 1, 6, 14, 30, tzinfo=UTC), 1)[0].hour == 16
    assert not daytime(NOW + timedelta(days=5), planning=True)
    assert slots(NOW + timedelta(hours=9, minutes=28), 20) == []


def test_windows_sleep_prevention_releases_after_hours() -> None:
    """Only the worker thread's Calgary work window prevents sleep; no power plan is edited."""
    from app.operations.awake import keep_awake

    flags: list[int] = []

    def setter(value: int) -> int:
        """Record Windows flags without changing the test machine."""
        flags.append(value)
        return 1

    keep_awake(NOW, enabled=True, setter=setter)
    keep_awake(NOW + timedelta(hours=10), enabled=True, setter=setter)
    keep_awake(NOW, enabled=False, setter=setter)
    assert flags == [0x80000001, 0x80000000, 0x80000000]
