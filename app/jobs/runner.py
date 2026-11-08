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
from app.jobs.heartbeat import keep_alive
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
        with keep_alive(db, settings, owner, job, clock):
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
    if settings.app_mode == "VERCEL":
        raise RuntimeError("Run the dedicated worker with a PC or VPS profile")
    owner = new_id()
    if not acquire_worker(db, settings, owner, utc_now()):
        raise RuntimeError("Another worker is active. Stop it or wait for its lease to expire.")
    logger.info("Worker started")
    try:
        while True:
            from app.sending.schedule import plan_jobs

            plan_jobs(db, settings, utc_now())
            run_once(db, settings, owner, handler=lambda job: dispatch_job(db, job, settings))
            if once:
                break
            time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopping")
    finally:
        release_worker(db, owner, utc_now())


def dispatch_job(db: Database, job: Job, settings: Settings | None = None) -> str:
    """Run an explicitly registered handler; imports are idempotent by source key."""
    if job.kind == "health":
        return health_handler(job)
    if job.kind == "calgary":
        from app.integrations.http import FetchError
        from app.sources.calgary import discover
        from app.sources.importing import import_candidates

        try:
            rows = discover(job.payload["category"])
            if not rows:
                return "No matching licensed businesses were returned."
            run = import_candidates(
                db, job.payload["campaign_id"], rows, "open_calgary", job.payload["actor_id"]
            )
            return f"Imported {run.imported} candidates; matched {run.duplicates} existing records."
        except FetchError as error:
            if error.temporary:
                raise TemporaryJobError("Source temporarily unavailable") from error
            raise PermanentJobError("Source unavailable") from error
    if job.kind == "audit":
        from app.audit.service import research

        return research(db, job)
    if job.kind == "assess" and settings is not None:
        from app.audit.ai import assess

        return assess(db, settings, job.payload["company_id"], job.payload["campaign_id"], job.id)
    if job.kind == "draft" and settings is not None:
        from app.drafting.ai import generate

        return generate(db, settings, job.payload, job.id)
    if settings is not None and job.kind == "inbox":
        from app.inbox.service import sync_inbox

        if not sync_inbox(db, settings, str(job.payload["mailbox_id"])):
            raise TemporaryJobError("Inbox sync incomplete")
        return "Inbox synchronized."
    if settings is not None and job.kind == "send":
        from app.sending.service import dispatch

        return dispatch(db, settings, job)
    if settings is not None and job.kind == "reconcile":
        from app.sending.service import reconcile

        return (
            "Sent copy confirmed."
            if reconcile(db, settings, str(job.payload["intent_id"]))
            else "No matching Sent evidence; held."
        )
    raise PermanentJobError("Unknown job handler")
