"""A separate worker; never run long-lived tasks inside an HTTP request."""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from random import SystemRandom

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job, new_id
from app.db.session import Database
from app.jobs.queue import acquire_worker, claim_next, finish_job, release_worker

logger = logging.getLogger(__name__)


class TemporaryJobError(Exception):
    """A handler failed before side effects and can safely be retried."""


class PermanentJobError(Exception):
    """A handler cannot succeed without a configuration or code change."""


def health_handler(job: Job) -> str:
    """Perform the only enabled Phase 1 job without any external calls."""
    if job.kind != "health":
        raise PermanentJobError("Handler unavailable in Phase 1")
    return "The database queue and worker are responding."


def run_once(
    db: Database,
    settings: Settings,
    owner: str,
    *,
    clock: Callable[[], datetime] = utc_now,
    handler: Callable[[Job], str] = health_handler,
) -> bool:
    """Run at most one claimed job and preserve failures as actionable state."""
    now = clock()
    if not acquire_worker(db, settings, owner, now):
        return False
    job = claim_next(db, settings, owner, now)
    if job is None:
        return False
    try:
        result = handler(job)
    except TemporaryJobError:
        retry = None
        if job.attempts < settings.job_max_attempts:
            delay = settings.job_retry_seconds * 2 ** (job.attempts - 1)
            retry = clock() + timedelta(seconds=delay + SystemRandom().uniform(0, 1))
        finish_job(db, job, clock(), error_code="temporary_failure", retry_at=retry)
        logger.warning("Job temporary failure id=%s", job.id)
    except PermanentJobError:
        finish_job(db, job, clock(), error_code="handler_unavailable")
        logger.error("Job unavailable id=%s", job.id)
    except Exception:
        # Persist and log the failure, but never log potentially sensitive exception text.
        finish_job(db, job, clock(), error_code="unexpected_failure")
        logger.error("Job unexpected failure id=%s", job.id)
    else:
        if not finish_job(db, job, clock(), result=result):
            logger.warning("Job stale claim discarded id=%s", job.id)
    return True


def run_worker(db: Database, settings: Settings, *, once: bool = False) -> None:
    """Poll until interrupted, or process one job for a supervised smoke test."""
    owner = new_id()
    if not acquire_worker(db, settings, owner, utc_now()):
        raise RuntimeError("Another worker is active. Stop it or wait for its lease to expire.")
    logger.info("Worker started")
    try:
        while True:
            run_once(db, settings, owner)
            if once:
                break
            time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopping")
    finally:
        release_worker(db, owner, utc_now())
