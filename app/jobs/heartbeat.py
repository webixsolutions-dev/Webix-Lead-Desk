"""Lease renewal during bounded external work; expired owners cannot revive a claim."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Event, Thread

from sqlalchemy import update

from app.core.config import Settings
from app.db.models import Job, WorkerInstance
from app.db.session import Database

logger = logging.getLogger(__name__)


def renew(db: Database, settings: Settings, owner: str, job: Job, now: datetime) -> bool:
    """Extend a live worker and its live claim in one transaction, otherwise roll back."""
    with db.sessions.begin() as session:
        worker = session.scalar(
            update(WorkerInstance)
            .where(
                WorkerInstance.key == "primary",
                WorkerInstance.owner_id == owner,
                WorkerInstance.lease_expires_at > now,
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=settings.worker_lease_seconds),
            )
            .returning(WorkerInstance.key)
        )
        claimed = session.scalar(
            update(Job)
            .where(
                Job.id == job.id,
                Job.state == "running",
                Job.claim_token == job.claim_token,
                Job.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds))
            .returning(Job.id)
        )
        if worker is None or claimed is None:
            session.rollback()
            return False
    return True


@contextmanager
def keep_alive(
    db: Database, settings: Settings, owner: str, job: Job, clock: Callable[[], datetime]
) -> Iterator[None]:
    """Renew while work runs and stop promptly when the handler exits."""
    stop = Event()

    def heartbeat() -> None:
        """Stop on any renewal failure; a stale result will be fenced by finalization."""
        interval = min(settings.worker_lease_seconds, settings.job_lease_seconds) / 3
        while not stop.wait(interval):
            try:
                if not renew(db, settings, owner, job, clock()):
                    logger.error("Worker lease lost id=%s", job.id)
                    return
            except Exception:
                logger.error("Worker lease renewal failed id=%s", job.id)
                return

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=6)
