"""Fence new work and conservatively settle expired work before offline backup or transfer."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.models import AuditEvent, Job, JobAttempt, WorkerInstance
from app.db.operations_models import RuntimeControl
from app.db.session import Database
from app.sending.recovery import hold_intent


def maintenance_enabled(session: Session) -> bool:
    """Read the shared maintenance gate inside the caller's database transaction."""
    control = session.get(RuntimeControl, "primary")
    return bool(control and control.maintenance)


def set_maintenance(db: Database, enabled: bool, note: str) -> None:
    """Record a local administrator's explicit stop or resume decision."""
    if not 10 <= len(note.strip()) <= 500:
        raise ValueError("Record a specific maintenance or resume reason")
    with db.sessions.begin() as session:
        control = session.get(RuntimeControl, "primary")
        if control is None:
            control = RuntimeControl(key="primary", generation=0)
            session.add(control)
        control.maintenance, control.note, control.updated_at = enabled, note.strip(), utc_now()
        control.generation += 1
        session.add(
            AuditEvent(
                action="maintenance.enabled" if enabled else "maintenance.disabled",
                details={"generation": control.generation},
            )
        )


def quiesce(db: Database, *, offline_confirmed: bool) -> None:
    """Require stopped processes, fence expired claims and retain every uncertain SMTP identity."""
    if not offline_confirmed:
        raise ValueError("Stop web and worker processes, then pass --offline-confirmed")
    now = utc_now()
    with db.sessions.begin() as session:
        if not maintenance_enabled(session):
            raise ValueError("Enable maintenance before backup or transfer")
        worker = session.get(WorkerInstance, "primary")
        if worker and worker.lease_expires_at > now:
            raise ValueError("A worker lease is still live; stop it and wait for lease expiry")
        jobs = list(session.scalars(select(Job).where(Job.state == "running")))
        if any(job.lease_expires_at and job.lease_expires_at > now for job in jobs):
            raise ValueError("A job lease is still live; wait for its expiry")
        for item in session.scalars(select(SendIntent).where(SendIntent.state == "transmitting")):
            hold_intent(session, item, now)
        for job in jobs:
            job.state, job.error_code, job.finished_at = "failed", "maintenance_recovery", now
            for attempt in session.scalars(
                select(JobAttempt).where(
                    JobAttempt.job_id == job.id, JobAttempt.outcome == "running"
                )
            ):
                attempt.outcome, attempt.finished_at = "interrupted", now
        session.flush()
