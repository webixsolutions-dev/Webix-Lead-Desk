"""Owner-approved, fixed-content mailbox tests using the existing durable queue."""

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.vault import cipher
from app.db.delivery_models import IntegrationSecret
from app.db.models import AuditEvent, Job, User, WorkerInstance
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.sending.providers import connection
from app.sources.validation import email_address

SUBJECT = "Webix Lead Desk - email connection test"
BODY = (
    "This is a one-time email connection test from Webix Lead Desk.\n"
    "Please confirm that this message arrived."
)
STARTED = "Test transmission started; outcome not yet confirmed."
RECEIVED = "Received in the test inbox (confirmed by owner)."
MAX_TESTS_PER_DAY = 3


def worker_ready(worker: WorkerInstance | None, now: datetime) -> bool:
    """Require a fresh worker that explicitly advertises the diagnostic capability."""
    return bool(
        worker
        and worker.lease_expires_at > now
        and (worker.runtime_config or {}).get("mailbox_tests") is True
    )


def request_test(
    db: Database,
    settings: Settings,
    mailbox_id: str,
    recipient: str,
    request_id: str,
    actor_id: str,
    now: datetime,
) -> Job:
    """Atomically approve one fixed message; repeated clicks share one immutable job."""
    request_id = str(UUID(request_id))
    recipient = email_address(recipient)
    if not recipient:
        raise ValueError("Enter one test email address you own or have permission to contact")
    with db.sessions() as session:
        actor = session.get(User, actor_id)
        mailbox = session.get(Mailbox, mailbox_id)
        if not actor or not actor.active or actor.role != "owner":
            raise ValueError("Only an active owner can approve a test email")
        if not mailbox or mailbox.provider not in {"titan", "hostinger", "gmail"}:
            raise ValueError("Choose an existing mailbox")
        existing = session.get(Job, request_id)
        if existing:
            if existing.kind != "mailbox_test" or any(
                existing.payload.get(key) != value
                for key, value in {
                    "mailbox_id": mailbox_id,
                    "recipient": recipient,
                    "actor_id": actor_id,
                }.items()
            ):
                raise ValueError("This test request was already used for another message")
            return existing
        if not worker_ready(session.get(WorkerInstance, "primary"), now):
            raise ValueError("Start the updated PC worker (v0.21 or newer) before sending a test")
        previous = session.scalars(
            select(Job).where(
                Job.kind == "mailbox_test",
                Job.payload["mailbox_id"].as_string() == mailbox_id,
                Job.payload["recipient"].as_string() == recipient,
            )
        )
        if any(
            item.id != request_id
            and (
                item.state in {"queued", "running"}
                or item.result == STARTED
                or (item.result or "").startswith("Outcome unknown")
            )
            for item in previous
        ):
            raise ValueError(
                "A test for this inbox is pending or uncertain; check its result first"
            )
        if not session.get(IntegrationSecret, "mailbox:" + mailbox_id):
            raise ValueError("Save the mailbox connection first")
        snapshot = {
            "version": 1,
            "job_id": request_id,
            "mailbox_id": mailbox_id,
            "recipient": recipient,
            "actor_id": actor_id,
            "issued_at": now.isoformat(),
            "from_email": mailbox.email,
            "from_name": mailbox.sender_name,
            "brand": mailbox.brand,
            "provider": mailbox.provider,
            "connection": connection(mailbox.provider, mailbox.connection),
            "subject": SUBJECT,
            "body": BODY,
        }
    approval = cipher(settings).encrypt(json.dumps(snapshot, sort_keys=True).encode()).decode()
    for slot in range(1, MAX_TESTS_PER_DAY + 1):
        try:
            with db.sessions.begin() as session:
                job = Job(
                    id=request_id,
                    unique_key=f"mailbox-test:{mailbox_id}:{now.date().isoformat()}:{slot}",
                    kind="mailbox_test",
                    payload={
                        "mailbox_id": mailbox_id,
                        "recipient": recipient,
                        "actor_id": actor_id,
                        "approval": approval,
                    },
                    due_at=now,
                    created_at=now,
                )
                session.add(job)
                session.add(
                    AuditEvent(
                        actor_id=actor_id, action="mailbox.test_approved", entity_id=request_id
                    )
                )
                session.flush()
                return job
        except IntegrityError:
            with db.sessions() as session:
                existing = session.get(Job, request_id)
                if (
                    existing
                    and existing.kind == "mailbox_test"
                    and all(
                        existing.payload.get(key) == value
                        for key, value in {
                            "mailbox_id": mailbox_id,
                            "recipient": recipient,
                            "actor_id": actor_id,
                        }.items()
                    )
                ):
                    return existing
                if existing:
                    raise ValueError("This test request was already used") from None
                occupied = session.scalar(
                    select(Job.id).where(
                        Job.unique_key
                        == (f"mailbox-test:{mailbox_id}:{now.date().isoformat()}:{slot}")
                    )
                )
                if not occupied:
                    raise
    raise ValueError("Three tests have already been requested for this mailbox today (UTC)")


def approved_snapshot(settings: Settings, job: Job, now: datetime) -> dict[str, Any]:
    """Unlock the exact approved message, rejecting changes, expiry and unsupported content."""
    from cryptography.fernet import InvalidToken

    try:
        data = json.loads(cipher(settings).decrypt(job.payload["approval"].encode()))
        issued = datetime.fromisoformat(data["issued_at"])
        if (
            not isinstance(data, dict)
            or data["version"] != 1
            or data["job_id"] != job.id
            or data["subject"] != SUBJECT
            or data["body"] != BODY
            or not -timedelta(minutes=1) <= now - issued <= timedelta(hours=24)
            or any(data[key] != job.payload[key] for key in ("recipient", "actor_id", "mailbox_id"))
            or email_address(data["recipient"]) != data["recipient"]
            or not data["recipient"]
        ):
            raise ValueError("Invalid test approval")
        return dict(data)
    except (InvalidToken, KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("Test approval is invalid or older than 24 hours") from error


def can_confirm(job: Job) -> bool:
    """Only a completed or uncertain transmission can receive an owner receipt."""
    return (
        job.state == "succeeded"
        and (job.result or "").startswith(("Accepted by the mail server", "Outcome unknown"))
    ) or (job.state == "failed" and job.result == STARTED)


def confirm_receipt(db: Database, mailbox_id: str, job_id: str, actor_id: str) -> None:
    """Record an owner's actual inbox observation; never change outreach readiness or resend."""
    with db.sessions.begin() as session:
        actor = session.get(User, actor_id)
        job = session.get(Job, job_id)
        if (
            not actor
            or not actor.active
            or actor.role != "owner"
            or not job
            or (job.kind != "mailbox_test" or job.payload.get("mailbox_id") != mailbox_id)
            or not can_confirm(job)
        ):
            raise ValueError("Choose an accepted or uncertain test email you actually received")
        session.add(
            AuditEvent(
                actor_id=actor_id,
                action="mailbox.test_received",
                entity_id=job.id,
                details={"previous_result": job.result},
            )
        )
        job.result = RECEIVED


def diagnostic_context(db: Database, now: datetime) -> dict[str, Any]:
    """Return safe UI metadata, without approval tokens or mailbox credentials."""
    with db.sessions() as session:
        recent = list(
            session.scalars(
                select(Job)
                .where(Job.kind == "mailbox_test")
                .order_by(Job.created_at.desc())
                .limit(30)
            )
        )
        tests: dict[str, list[dict[str, Any]]] = {}
        for job in recent:
            status = job.result or (
                "Waiting for the worker" if job.state == "queued" else "Checking mailbox connection"
            )
            if job.state == "failed" and job.result != RECEIVED:
                status = (
                    "Outcome unknown; do not resend this request"
                    if job.result == STARTED
                    else "Test stopped before completion; review the connection"
                )
            tests.setdefault(str(job.payload.get("mailbox_id", "")), []).append(
                {
                    "id": job.id,
                    "recipient": job.payload.get("recipient", ""),
                    "created_at": job.created_at,
                    "status": status,
                    "can_confirm": can_confirm(job),
                }
            )
        return {
            "mailbox_tests": tests,
            "mailbox_test_ready": worker_ready(session.get(WorkerInstance, "primary"), now),
            "mailbox_test_subject": SUBJECT,
            "mailbox_test_body": BODY,
        }
