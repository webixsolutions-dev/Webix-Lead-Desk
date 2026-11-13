"""A separate worker; never run long-lived tasks inside an HTTP request."""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from random import SystemRandom
from threading import Event

import psycopg
from sqlalchemy.exc import DBAPIError

from app.audit.batch import BatchPending
from app.audit.budget import BudgetBlocked
from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job, new_id
from app.db.session import Database
from app.integrations.openai import ModelUnavailable
from app.jobs.heartbeat import keep_alive
from app.jobs.queue import acquire_worker, claim_next, finish_job, release_worker
from app.operations.awake import keep_awake

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
    except BatchPending:
        finish_job(
            db,
            job,
            clock(),
            result="Batch waiting",
            retry_at=clock() + timedelta(minutes=5),
            polling=True,
        )
    except TemporaryJobError:
        retry = None
        if job.attempts < settings.job_max_attempts:
            delay = settings.job_retry_seconds * 2 ** (job.attempts - 1)
            retry = clock() + timedelta(seconds=delay + SystemRandom().uniform(0, 1))
        finish_job(db, job, clock(), error_code="temporary_failure", retry_at=retry)
        logger.warning("Job temporary failure id=%s", job.id)
    except (BudgetBlocked, ModelUnavailable, ValueError) as error:
        from app.sources.enrichment.flow import hold

        if job.payload.get("company_id"):
            hold(
                db,
                job.payload["company_id"],
                job.id,
                str(error)[:300]
                if isinstance(error, BudgetBlocked)
                else "Research or configuration needs review; inspect the failed job",
            )
        code = (
            "budget_or_cache_blocked"
            if isinstance(error, BudgetBlocked)
            else "provider_or_input_review"
        )
        finish_job(
            db,
            job,
            clock(),
            error_code=code,
            result=str(error)[:300] if isinstance(error, BudgetBlocked) else None,
        )
        logger.warning("Job held id=%s code=%s", job.id, code)
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


def run_worker(
    db: Database,
    settings: Settings,
    *,
    once: bool = False,
    stop_event: Event | None = None,
    on_started: Callable[[], None] | None = None,
) -> None:
    """Poll until interrupted, or process one job for a supervised smoke test."""
    if settings.app_mode == "VERCEL":
        raise RuntimeError("Run the dedicated worker with a PC or VPS profile")
    owner = new_id()
    if not acquire_worker(db, settings, owner, utc_now()):
        raise RuntimeError("Another worker is active. Stop it or wait for its lease to expire.")
    logger.info("Worker started")
    next_linkedin_plan = utc_now()
    try:
        if on_started is not None:
            on_started()
        while stop_event is None or not stop_event.is_set():
            from app.linkedin.planner import plan_day
            from app.sending.schedule import plan_jobs

            try:
                keep_awake(utc_now(), enabled=settings.app_mode == "PC" and settings.pc_keep_awake)
                if utc_now() >= next_linkedin_plan:
                    from app.linkedin.learning import tick as linkedin_learning_tick

                    linkedin_learning_tick(db, utc_now())
                    plan_day(db, utc_now())
                    from app.linkedin.posting import plan as plan_posts

                    plan_posts(db, settings, utc_now())
                    next_linkedin_plan = utc_now() + timedelta(
                        seconds=settings.linkedin_plan_interval_seconds
                    )
                from app.operations.scheduled_backup import scheduled_backup

                scheduled_backup(db, settings, utc_now())
                from app.sending.delegation import tick as delegation_tick

                delegation_tick(db, settings)
                plan_jobs(db, settings, utc_now())
                from app.whatsapp.worker import plan as plan_whatsapp

                plan_whatsapp(db, settings, utc_now())
                if stop_event is not None and stop_event.is_set():
                    break
                run_once(db, settings, owner, handler=lambda job: dispatch_job(db, job, settings))
            except DBAPIError as error:
                from app.db.connection import transient

                if not (
                    error.connection_invalidated
                    or isinstance(error.orig, psycopg.OperationalError)
                    and transient(error.orig)
                ):
                    raise
                logger.warning("Database connection interrupted; unfinished claims remain fenced")
                if once:
                    raise
                db.engine.dispose()
                if stop_event is None:
                    time.sleep(min(30, settings.job_retry_seconds))
                else:
                    stop_event.wait(min(30, settings.job_retry_seconds))
                continue
            if once:
                break
            if stop_event is None:
                time.sleep(settings.worker_poll_seconds)
            else:
                stop_event.wait(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopping")
    finally:
        keep_awake(utc_now(), enabled=False)
        try:
            release_worker(db, owner, utc_now())
        except DBAPIError:
            logger.warning("Worker disconnected; its lease will expire automatically")


def dispatch_job(db: Database, job: Job, settings: Settings | None = None) -> str:
    """Run an explicitly registered handler; imports are idempotent by source key."""
    if settings is not None and job.kind == "whatsapp":
        from app.whatsapp.worker import dispatch as whatsapp_dispatch

        return whatsapp_dispatch(db, settings, job.payload["generation"])
    if job.kind == "health":
        return health_handler(job)
    if settings is not None and job.kind == "linkedin_post":
        from app.linkedin.posting import dispatch as publish_post

        return publish_post(db, settings, job.payload["post_id"])
    if settings is not None and job.kind == "linkedin_draft":
        from app.linkedin.ai import generate as generate_linkedin

        return generate_linkedin(db, settings, job.payload["draft_id"], job.id)
    if settings is not None and job.kind == "find_owner":
        from app.linkedin.finder import find_owners

        return find_owners(
            db, settings, job.payload["company_id"], job.payload["campaign_id"], job.id
        )
    if job.kind == "score":
        from app.scoring.service import score

        item = score(db, job.payload["company_id"], job.payload["campaign_id"])
        return f"Rule score updated: {item.points}/10 · {item.band}"
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
    if settings is not None and job.kind in {
        "discover",
        "linkedin_discover",
        "enrich_company",
        "advance_company",
    }:
        from app.sources.enrichment.flow import advance, enrich_company
        from app.sources.enrichment.flow import discover as discover_city

        handler = {
            "discover": discover_city,
            "linkedin_discover": discover_city,
            "enrich_company": enrich_company,
            "advance_company": advance,
        }[job.kind]
        return handler(db, settings, job)
    if settings is not None and job.kind == "call_notes":
        from app.calls.notes import clean_notes

        return clean_notes(db, settings, job.payload["call_id"], job.id)
    if job.kind == "audit":
        from app.audit.service import research

        result = research(db, job, settings=settings)
        if job.payload.get("continue_flow"):
            from app.jobs.queue import enqueue

            enqueue(db, "audited:" + job.id, "advance_company", job.payload, utc_now())
        return result
    if job.kind == "assess" and settings is not None:
        from app.audit.ai import assess

        result = assess(db, settings, job.payload["company_id"], job.payload["campaign_id"], job.id)
        if job.payload.get("continue_flow"):
            from app.audit.outcomes import may_draft
            from app.jobs.queue import enqueue

            with db.sessions() as session:
                ready = may_draft(session, job.payload["company_id"], job.payload["campaign_id"])
            if ready:
                enqueue(db, "assessed:" + job.id, "draft", job.payload, utc_now())
        return result
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
    if settings is not None and job.kind == "mailbox_test":
        from app.sending.diagnostic_worker import send_test

        return send_test(db, settings, job)
    if settings is not None and job.kind == "reconcile":
        from app.sending.service import reconcile

        return (
            "Sent copy confirmed."
            if reconcile(db, settings, str(job.payload["intent_id"]))
            else "No matching Sent evidence; held."
        )
    raise PermanentJobError("Unknown job handler")
