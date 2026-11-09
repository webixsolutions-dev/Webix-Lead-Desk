"""One diagnostic SMTP attempt, isolated from outreach and never blindly retried."""

from collections.abc import Callable
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from typing import Any

from sqlalchemy import update

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.models import Job, User, WorkerInstance
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.integrations.dns_mail import check_mail_route
from app.integrations.smtp import SMTPResult, transmit
from app.sending.diagnostics import STARTED, approved_snapshot
from app.sending.providers import connection, mailbox_settings
from app.sending.signature_html import set_message_content
from app.sending.signatures import get_signature


def valid_claim(db: Database, job: Job, snapshot: dict[str, Any], now: datetime) -> bool:
    """Recheck the owner, sender, immutable approval and live queue lease before DATA."""
    with db.sessions() as session:
        current = session.get(Job, job.id)
        worker = session.get(WorkerInstance, "primary")
        actor = session.get(User, snapshot["actor_id"])
        box = session.get(Mailbox, snapshot["mailbox_id"])
        return bool(
            current
            and current.kind == "mailbox_test"
            and current.state == "running"
            and current.claim_token == job.claim_token
            and current.attempts == 1
            and current.lease_expires_at
            and current.lease_expires_at > now
            and current.payload == job.payload
            and worker
            and worker.owner_id == current.claim_owner
            and worker.lease_expires_at > now
            and (worker.runtime_config or {}).get("mailbox_tests") is True
            and actor
            and actor.active
            and actor.role == "owner"
            and box
            and get_signature(session, box.id)
            == snapshot.get("signature", {"html": "", "text": "", "version": 0})
            and (
                not snapshot.get("signature", {}).get("html")
                or (worker.runtime_config or {}).get("email_signatures") == 1
            )
            and box.email == snapshot["from_email"]
            and box.sender_name == snapshot["from_name"]
            and box.brand == snapshot["brand"]
            and box.provider == snapshot["provider"]
            and connection(box.provider, box.connection) == snapshot["connection"]
        )


def wire_message(snapshot: dict[str, Any], now: datetime) -> bytes:
    """Build a plain diagnostic with a stable Message-ID and exactly one recipient."""
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = formataddr((snapshot["from_name"], snapshot["from_email"]))
    message["To"] = snapshot["recipient"]
    message["Subject"] = snapshot["subject"]
    message["Date"] = format_datetime(now)
    message["Message-ID"] = (
        f"<mailbox-test-{snapshot['job_id']}@{snapshot['from_email'].split('@')[1]}>"
    )
    content = dict(snapshot)
    if not (snapshot.get("signature") or {}).get("html"):
        content["footer"] = f"{snapshot['from_name']}\n{snapshot['brand']}"
    set_message_content(message, content)
    return message.as_bytes()


def send_test(
    db: Database,
    settings: Settings,
    job: Job,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> str:
    """Consume a signed test request once; production outreach stays independently disabled."""
    if settings.app_mode == "VERCEL" or not settings.mailbox_tests_enabled:
        return "Test blocked: this process cannot send test emails."
    try:
        snapshot = approved_snapshot(settings, job, clock())
        if not valid_claim(db, job, snapshot, clock()):
            return "Test blocked: approval, sender or worker changed."
        with db.sessions() as session:
            box = session.get(Mailbox, snapshot["mailbox_id"])
            assert box
            provider_settings = mailbox_settings(settings, box)
        password = read_secret(db, settings, "mailbox:" + snapshot["mailbox_id"])
        wire = wire_message(snapshot, clock())
        if check_mail_route(snapshot["recipient"]) not in {"mx", "implicit_mx"}:
            return "Test blocked: recipient email routing could not be verified."
    except ValueError:
        return "Test blocked: check the saved credential, sender and approval."
    # A durable marker precedes SMTP. A crash or repeated handler call cannot send twice.
    with db.sessions.begin() as session:
        reserved = session.scalar(
            update(Job)
            .where(
                Job.id == job.id,
                Job.kind == "mailbox_test",
                Job.state == "running",
                Job.claim_token == job.claim_token,
                Job.attempts == 1,
                Job.lease_expires_at > clock(),
                Job.result.is_(None),
            )
            .values(result=STARTED)
            .returning(Job.id)
        )
    if not reserved:
        return "Test already attempted; no repeat transmission was made."
    try:
        result = transmit(
            provider_settings,
            snapshot["from_email"],
            password,
            snapshot["recipient"],
            wire,
            before_data=lambda: valid_claim(db, job, snapshot, clock()),
        )
    except Exception:
        # Exceptions after reservation are ambiguous, including response persistence failures.
        result = SMTPResult("uncertain", "unexpected_transport_failure")
    if result.outcome == "accepted":
        return "Accepted by the mail server. Check the recipient inbox and spam folder."
    if result.outcome == "uncertain":
        return "Outcome unknown. Check the recipient inbox before requesting another test."
    return f"Test not sent ({result.code}). Check mailbox settings before a new test."
