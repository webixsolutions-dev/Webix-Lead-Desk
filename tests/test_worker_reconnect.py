"""Dropped connections resume queue polling without replaying send side effects."""

from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job, WorkerInstance
from app.db.session import Database
from app.jobs import runner
from app.jobs.queue import enqueue, enqueue_health, finish_job


def test_reconnect_does_not_replay_interrupted_send(
    db: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lost completion write holds the send and lets a later health job complete."""
    now = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
    sent = enqueue(db, "offline-send-probe", "send", {}, now)
    health = enqueue_health(db, "after-reconnect", now + timedelta(seconds=1))
    calls: list[str] = []
    sleeps: list[float] = []

    def dispatch(database: Database, job: Job, config: Settings) -> str:
        """Record an offline side effect without ever invoking a mail transport."""
        calls.append(job.kind)
        return "Offline handler completed"

    def finish(database: Database, job: Job, clock: datetime, *, result: str) -> bool:
        """Lose the acknowledgement write after the synthetic send side effect."""
        if job.kind == "send":
            raise DBAPIError(
                None,
                None,
                psycopg.OperationalError("PRIVATE_SYNTHETIC_CONNECTION_DETAIL"),
                connection_invalidated=True,
            )
        return finish_job(database, job, clock, result=result)

    def sleep(seconds: float) -> None:
        """Advance past the lost claim once, then stop after the recovered poll."""
        nonlocal now
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt
        now += timedelta(seconds=settings.job_lease_seconds + 1)

    monkeypatch.setattr(runner, "utc_now", lambda: now)
    monkeypatch.setattr(runner, "run_once", partial(runner.run_once, clock=lambda: now))
    monkeypatch.setattr(runner, "dispatch_job", dispatch)
    monkeypatch.setattr(runner, "finish_job", finish)
    monkeypatch.setattr("app.jobs.runner.time.sleep", sleep)
    runner.run_worker(db, settings)

    assert calls == ["send", "health"]
    assert sleeps == [settings.job_retry_seconds, settings.worker_poll_seconds]
    with db.sessions() as session:
        interrupted = session.get(Job, sent.id)
        completed = session.get(Job, health.id)
        worker = session.get(WorkerInstance, "primary")
        assert interrupted is not None and interrupted.state == "failed"
        assert interrupted.error_code == "lease_expired" and interrupted.attempts == 1
        assert completed is not None and completed.state == "succeeded"
        assert worker is not None and worker.lease_expires_at <= now
    assert "PRIVATE_SYNTHETIC" not in caplog.text


@pytest.mark.parametrize("once, transient", [(True, True), (False, False)])
def test_unrecoverable_worker_errors_exit_without_polling_again(
    db: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    once: bool,
    transient: bool,
) -> None:
    """One-shot failures and permanent database errors stay visible to the supervisor."""
    error = DBAPIError(
        None,
        None,
        psycopg.OperationalError("Synthetic interruption")
        if transient
        else psycopg.ProgrammingError("Synthetic permanent error"),
    )
    planner = Mock(side_effect=error)
    sleeper = Mock()
    monkeypatch.setattr("app.sending.schedule.plan_jobs", planner)
    monkeypatch.setattr("app.jobs.runner.time.sleep", sleeper)
    with pytest.raises(DBAPIError) as caught:
        runner.run_worker(db, settings, once=once)
    assert caught.value is error
    planner.assert_called_once()
    sleeper.assert_not_called()


def test_disconnect_during_shutdown_does_not_undo_completed_job(
    db: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed lease release leaves completed work intact and logs no connection details."""
    saved = enqueue_health(db, "shutdown-disconnect", utc_now())
    monkeypatch.setattr(
        runner,
        "release_worker",
        Mock(
            side_effect=DBAPIError(
                None, None, psycopg.OperationalError("PRIVATE_SYNTHETIC_CONNECTION_DETAIL")
            )
        ),
    )
    runner.run_worker(db, settings, once=True)
    with db.sessions() as session:
        completed = session.get(Job, saved.id)
        assert completed is not None and completed.state == "succeeded"
    assert "lease will expire" in caplog.text
    assert "PRIVATE_SYNTHETIC" not in caplog.text
