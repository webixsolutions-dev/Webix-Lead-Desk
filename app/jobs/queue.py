"""Atomic database queue operations for the Phase 1 health handler."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.models import Job, JobAttempt, WorkerInstance, new_id
from app.db.session import Database


def enqueue_health(db: Database, unique_key: str, now: datetime) -> Job:
    """Enqueue once or return an existing health job with the same key."""
    return enqueue(db, unique_key, "health", {}, now)


def enqueue(
    db: Database, unique_key: str, kind: str, payload: dict[str, Any], now: datetime
) -> Job:
    """Enqueue bounded reference data with a unique business key."""
    if kind not in {"health", "calgary", "audit", "assess", "draft", "inbox", "send", "reconcile"}:
        raise ValueError("Unknown job kind")
    if not 1 <= len(unique_key) <= 128:
        raise ValueError("Job key must contain 1–128 characters")
    try:
        with db.sessions.begin() as session:
            job = Job(unique_key=unique_key, kind=kind, due_at=now, payload=payload)
            session.add(job)
            session.flush()
            return job
    except IntegrityError:
        with db.sessions() as session:
            existing = session.scalar(select(Job).where(Job.unique_key == unique_key))
            if existing is None:
                raise
            return existing


def acquire_worker(db: Database, settings: Settings, owner: str, now: datetime) -> bool:
    """Acquire or refresh the singleton lease without displacing a live worker."""
    runtime = {"app_mode": settings.app_mode, "live_send_enabled": settings.live_send_enabled}
    expiry = now + timedelta(seconds=settings.worker_lease_seconds)
    with db.sessions.begin() as session:
        if session.get(WorkerInstance, "primary") is None:
            try:
                with session.begin_nested():
                    session.add(
                        WorkerInstance(
                            key="primary", owner_id=owner, heartbeat_at=now, lease_expires_at=expiry
                        )
                    )
                    session.flush()
            except IntegrityError:
                pass  # The competing insert is resolved by the conditional update below.
        result = session.scalar(
            update(WorkerInstance)
            .where(
                WorkerInstance.key == "primary",
                or_(WorkerInstance.owner_id == owner, WorkerInstance.lease_expires_at <= now),
            )
            .values(
                owner_id=owner, heartbeat_at=now, lease_expires_at=expiry, runtime_config=runtime
            )
            .returning(WorkerInstance.key)
        )
        return result is not None


def release_worker(db: Database, owner: str, now: datetime) -> None:
    """Release only a lease owned by the exiting process."""
    with db.sessions.begin() as session:
        session.execute(
            update(WorkerInstance)
            .where(
                WorkerInstance.key == "primary",
                WorkerInstance.owner_id == owner,
            )
            .values(lease_expires_at=now)
        )


def claim_next(db: Database, settings: Settings, owner: str, now: datetime) -> Job | None:
    """Recover expired health work and atomically claim one due job."""
    with db.sessions.begin() as session:
        lease = session.get(WorkerInstance, "primary")
        if lease is None or lease.owner_id != owner or lease.lease_expires_at <= now:
            return None
        expired = session.scalars(
            select(Job).where(
                Job.kind.in_(
                    ["health", "calgary", "audit", "assess", "draft", "inbox", "send", "reconcile"]
                ),
                Job.state == "running",
                Job.lease_expires_at <= now,
            )
        ).all()
        for job in expired:
            if job.kind == "send":
                from app.sending.recovery import hold_expired_send

                hold_expired_send(session, job, now)
            session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.claim_token == job.claim_token,
                    JobAttempt.outcome == "running",
                )
                .values(finished_at=now, outcome="lease_expired")
            )
            job.state = (
                "failed"
                if job.kind in {"assess", "draft", "send"}
                or job.attempts >= settings.job_max_attempts
                else "queued"
            )
            job.finished_at = now if job.state == "failed" else None
            job.claim_owner = None
            job.claim_token = None
            job.lease_expires_at = None
            job.error_code = "lease_expired"
        session.flush()
        candidate = session.scalar(
            select(Job.id)
            .where(
                Job.state == "queued",
                Job.due_at <= now,
                Job.attempts < settings.job_max_attempts,
            )
            .order_by(Job.due_at, Job.created_at)
            .limit(1)
        )
        if candidate is None:
            return None
        claim = new_id()
        claimed = session.scalar(
            update(Job)
            .where(
                Job.id == candidate,
                Job.state == "queued",
            )
            .values(
                state="running",
                attempts=Job.attempts + 1,
                claim_token=claim,
                claim_owner=owner,
                lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
                error_code=None,
            )
            .returning(Job.id)
        )
        if claimed is None:
            return None
        session.add(JobAttempt(job_id=claimed, claim_token=claim, started_at=now))
        return session.get(Job, claimed)


def finish_job(
    db: Database,
    job: Job,
    now: datetime,
    *,
    result: str | None = None,
    error_code: str | None = None,
    retry_at: datetime | None = None,
) -> bool:
    """Finalize only a still-live claim, ignoring late results from stale workers."""
    outcome = "retry" if retry_at is not None else ("failed" if error_code else "succeeded")
    with db.sessions.begin() as session:
        changed = session.scalar(
            update(Job)
            .where(
                Job.id == job.id,
                Job.state == "running",
                Job.claim_token == job.claim_token,
                Job.lease_expires_at > now,
            )
            .values(
                state="queued" if retry_at else outcome,
                finished_at=None if retry_at else now,
                due_at=retry_at or job.due_at,
                result=result,
                error_code=error_code,
                claim_token=None,
                claim_owner=None,
                lease_expires_at=None,
            )
            .returning(Job.id)
        )
        if changed is None:
            return False
        session.execute(
            update(JobAttempt)
            .where(JobAttempt.claim_token == job.claim_token)
            .values(finished_at=now, outcome=outcome)
        )
        return True
