"""Durability, duplicate protection and failure recovery for health jobs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Job, JobAttempt, new_id
from app.db.session import Database
from app.jobs.queue import acquire_worker, claim_next, enqueue_health, finish_job, release_worker
from app.jobs.runner import PermanentJobError, TemporaryJobError, run_once, run_worker

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


def test_concurrent_enqueue_and_claim(db: Database, settings: Settings) -> None:
    """Concurrent clicks and claimers still create one job and one claim."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: enqueue_health(db, "concurrent", NOW).id, range(4)))
    assert len(set(ids)) == 1
    owner = new_id()
    assert acquire_worker(db, settings, owner, NOW)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: claim_next(db, settings, owner, NOW), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_queue_idempotency_and_success(db: Database, settings: Settings) -> None:
    """Duplicate requests share an ID and a completed job is never claimed again."""
    first = enqueue_health(db, "same-job", NOW)
    assert enqueue_health(db, "same-job", NOW).id == first.id
    owner = new_id()
    assert run_once(db, settings, owner, clock=lambda: NOW)
    assert not run_once(db, settings, owner, clock=lambda: NOW)
    with db.sessions() as session:
        job = session.get(Job, first.id)
        assert job is not None and job.state == "succeeded"
        assert job.attempts == 1 and job.result
        attempt = session.scalar(select(JobAttempt))
        assert attempt is not None and attempt.outcome == "succeeded"


def test_second_worker_cannot_take_live_lease(db: Database, settings: Settings) -> None:
    """One worker owns the queue, with explicit release and expired-lease takeover."""
    first, second = new_id(), new_id()
    assert acquire_worker(db, settings, first, NOW)
    assert not acquire_worker(db, settings, second, NOW)
    assert claim_next(db, settings, second, NOW) is None
    release_worker(db, second, NOW)
    assert not acquire_worker(db, settings, second, NOW)
    release_worker(db, first, NOW)
    assert acquire_worker(db, settings, second, NOW)
    assert acquire_worker(
        db, settings, first, NOW + timedelta(seconds=settings.worker_lease_seconds + 1)
    )


def test_crashed_health_job_recovers_without_stale_overwrite(
    db: Database, settings: Settings
) -> None:
    """A restart recovers idempotent work and rejects the previous worker's late result."""
    saved = enqueue_health(db, "recover", NOW)
    first = new_id()
    acquire_worker(db, settings, first, NOW)
    old_claim = claim_next(db, settings, first, NOW)
    assert old_claim is not None
    later = NOW + timedelta(seconds=settings.job_lease_seconds + settings.worker_lease_seconds + 1)
    assert run_once(db, settings, new_id(), clock=lambda: later)
    assert not finish_job(db, old_claim, later, result="stale overwrite")
    with db.sessions() as session:
        job = session.get(Job, saved.id)
        assert job is not None and job.state == "succeeded" and job.attempts == 2
        outcomes = set(session.scalars(select(JobAttempt.outcome)).all())
        assert outcomes == {"lease_expired", "succeeded"}


def test_temporary_failure_backoff_and_retry_cap(db: Database, settings: Settings) -> None:
    """Retries are delayed and eventually become a visible terminal failure."""
    job = enqueue_health(db, "retry", NOW)
    owner = new_id()
    now = NOW

    def temporary_failure(item: Job) -> str:
        """Simulate a transient failure without external side effects."""
        raise TemporaryJobError("not logged")

    def clock() -> datetime:
        """Return the current controlled retry time."""
        return now

    for attempt in range(settings.job_max_attempts):
        assert run_once(db, settings, owner, clock=clock, handler=temporary_failure)
        with db.sessions() as session:
            stored = session.get(Job, job.id)
            assert stored is not None
            if attempt < settings.job_max_attempts - 1:
                assert stored.state == "queued" and stored.due_at > now
                assert not run_once(db, settings, owner, clock=clock)
                now = stored.due_at + timedelta(seconds=1)
            else:
                assert stored.state == "failed"


@pytest.mark.parametrize("failure", [RuntimeError, PermanentJobError])
def test_permanent_and_unexpected_errors_are_redacted(
    db: Database, settings: Settings, failure: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected exception details never leak into stored state or logs."""
    saved = enqueue_health(db, "bad-handler", NOW)

    def fail(item: Job) -> str:
        """Raise deliberately sensitive-looking synthetic error text."""
        raise failure("DO_NOT_LOG_SYNTHETIC_PAYLOAD")

    assert run_once(db, settings, new_id(), clock=lambda: NOW, handler=fail)
    with db.sessions() as session:
        job = session.get(Job, saved.id)
        assert job is not None and job.state == "failed"
        assert "DO_NOT_LOG" not in (job.error_code or "")
    assert "DO_NOT_LOG" not in caplog.text


def test_future_jobs_wait_and_unknown_handlers_fail(db: Database, settings: Settings) -> None:
    """The worker respects due times and never executes arbitrary handler names."""
    saved = enqueue_health(db, "future", NOW + timedelta(hours=1))
    assert not run_once(db, settings, new_id(), clock=lambda: NOW)
    with db.sessions.begin() as session:
        job = session.get(Job, saved.id)
        assert job is not None
        job.kind = "send_email"
    assert run_once(db, settings, new_id(), clock=lambda: NOW + timedelta(hours=1))
    with db.sessions() as session:
        job = session.get(Job, saved.id)
        assert job is not None and job.error_code == "handler_unavailable"


def test_worker_once_and_duplicate_start(db: Database, settings: Settings) -> None:
    """The CLI's one-shot mode releases its lease and a duplicate start fails clearly."""
    run_worker(db, settings, once=True)
    from app.core.clock import utc_now

    acquire_worker(db, settings, new_id(), utc_now())
    with pytest.raises(RuntimeError, match="Another worker"):
        run_worker(db, settings, once=True)
