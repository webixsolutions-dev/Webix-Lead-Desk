"""Fixed-message SMTP diagnostics: explicit approval, worker fencing and no duplicates."""

import smtplib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.vault import store_secret
from app.db.models import Job, User, WorkerInstance, new_id
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.integrations.smtp import SMTPResult, transmit
from app.jobs.queue import acquire_worker, claim_next, finish_job
from app.main import create_app
from app.sending import diagnostic_worker
from app.sending.diagnostic_worker import send_test
from app.sending.diagnostics import (
    BODY,
    STARTED,
    SUBJECT,
    confirm_receipt,
    diagnostic_context,
    request_test,
)
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW
from tests.mail_servers import SMTPHandler, fake_server


@dataclass
class Setup:
    """Synthetic account and explicitly capable offline worker."""

    config: Settings
    mailbox: str
    actor: str
    worker: str


@pytest.fixture
def setup(db: Database, settings: Settings, owner: Account) -> Setup:
    """Keep outreach disabled while enabling only owner-approved diagnostic jobs."""
    config = settings.model_copy(
        update={
            "encryption_key": SecretStr(Fernet.generate_key().decode()),
            "mailbox_tests_enabled": True,
        }
    )
    with db.sessions.begin() as session:
        box = Mailbox(
            email="sales@example.org",
            provider="titan",
            lane="client",
            brand="Example Studio",
            sender_name="Test Sender",
            mailing_address="Synthetic address",
            contact_details="sales@example.org",
        )
        session.add(box)
        session.flush()
        mailbox = box.id
    store_secret(db, config, "mailbox:" + mailbox, "synthetic-only", owner.user.id)
    worker = new_id()
    assert acquire_worker(db, config, worker, NOW)
    return Setup(config, mailbox, owner.user.id, worker)


def approved_job(db: Database, setup: Setup) -> Job:
    """Approve and claim one bounded test for a controlled clock."""
    request_test(db, setup.config, setup.mailbox, "owner@example.com", new_id(), setup.actor, NOW)
    job = claim_next(db, setup.config, setup.worker, NOW)
    assert job
    return job


@pytest.mark.parametrize("drop", [False, True])
def test_one_smtp_attempt_with_lost_ack_and_duplicate_handler(
    db: Database, setup: Setup, monkeypatch: pytest.MonkeyPatch, drop: bool
) -> None:
    """A real stdlib SMTP session reaches only a loopback fake, once even after uncertainty."""
    job = approved_job(db, setup)
    monkeypatch.setattr(diagnostic_worker, "check_mail_route", lambda _: "mx")
    with fake_server(SMTPHandler, drop=drop) as server:

        def send(
            config: Settings,
            sender: str,
            password: str,
            recipient: str,
            wire: bytes,
            *,
            before_data: Callable[[], bool],
        ) -> SMTPResult:
            """Use the production transport against a synthetic local server."""
            return transmit(
                config,
                sender,
                password,
                recipient,
                wire,
                before_data=before_data,
                factory=lambda: smtplib.SMTP(
                    str(server.server_address[0]), int(server.server_address[1]), timeout=2
                ),
            )

        monkeypatch.setattr(diagnostic_worker, "transmit", send)
        result = send_test(db, setup.config, job, clock=lambda: NOW)
        assert ("Outcome unknown" if drop else "Accepted by the mail server") in result
        assert "already attempted" in send_test(db, setup.config, job, clock=lambda: NOW)
        assert len(server.messages) == 1
        message = BytesParser(policy=policy.default).parsebytes(server.messages[0])
        assert message["To"] == "owner@example.com" and message["Subject"] == SUBJECT
        assert BODY in str(message.get_content()).replace("\r\n", "\n")
        assert message["Bcc"] is None and message["Cc"] is None
        assert job.id in message["Message-ID"]
        assert finish_job(db, job, NOW, result=result)
        assert claim_next(db, setup.config, setup.worker, NOW) is None
    with db.sessions() as session:
        box = session.get(Mailbox, setup.mailbox)
        assert box and not box.enabled and not setup.config.live_send_enabled


def test_replayed_form_and_three_per_day_limit(db: Database, setup: Setup) -> None:
    """Idempotency survives concurrent repeated POSTs and distinct requests cannot exceed slots."""
    request_id = new_id()

    def submit(_: int) -> str:
        """Repeat the same owner-approved request."""
        return request_test(
            db, setup.config, setup.mailbox, "owner@example.com", request_id, setup.actor, NOW
        ).id

    with ThreadPoolExecutor(max_workers=3) as executor:
        assert set(executor.map(submit, range(3))) == {request_id}
    with pytest.raises(ValueError, match="another message"):
        request_test(
            db, setup.config, setup.mailbox, "changed@example.com", request_id, setup.actor, NOW
        )
    for index in range(2):
        request_test(
            db, setup.config, setup.mailbox, f"owner{index}@example.com", new_id(), setup.actor, NOW
        )
    with pytest.raises(ValueError, match="Three tests"):
        request_test(
            db, setup.config, setup.mailbox, "fourth@example.com", new_id(), setup.actor, NOW
        )


@pytest.mark.parametrize(
    "case", ["expired", "tampered", "owner", "sender", "lease", "disabled", "vercel"]
)
def test_invalid_approval_or_process_never_calls_smtp(
    db: Database, setup: Setup, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Reject mismatched content, stale approvals/leases, revoked owners and web processes."""
    job = approved_job(db, setup)
    now, config = NOW, setup.config
    with db.sessions.begin() as session:
        if case == "tampered":
            job.payload = {**job.payload, "recipient": "changed@example.com"}
            stored = session.get(Job, job.id)
            assert stored
            stored.payload = job.payload
        elif case == "owner":
            actor = session.get(User, setup.actor)
            assert actor
            actor.active = False
        elif case == "sender":
            box = session.get(Mailbox, setup.mailbox)
            assert box
            box.sender_name = "Changed Sender"
        elif case == "lease":
            worker = session.get(WorkerInstance, "primary")
            assert worker
            worker.lease_expires_at = NOW
    if case == "expired":
        now += timedelta(days=2)
    if case == "disabled":
        config = config.model_copy(update={"mailbox_tests_enabled": False})
    if case == "vercel":
        config = config.model_copy(update={"app_mode": "VERCEL"})
    sender = Mock()
    monkeypatch.setattr(diagnostic_worker, "transmit", sender)
    assert "blocked" in send_test(db, config, job, clock=lambda: now)
    sender.assert_not_called()


def test_crashed_test_is_not_reclaimed(db: Database, setup: Setup) -> None:
    """An expired claim is terminal and displays uncertainty if SMTP may have started."""
    job = approved_job(db, setup)
    with db.sessions.begin() as session:
        stored = session.get(Job, job.id)
        assert stored
        stored.result = STARTED
    later = NOW + timedelta(minutes=5)
    replacement = new_id()
    assert acquire_worker(db, setup.config, replacement, later)
    assert claim_next(db, setup.config, replacement, later) is None
    with db.sessions() as session:
        stored = session.get(Job, job.id)
        assert stored and stored.state == "failed" and stored.attempts == 1
    assert (
        "Outcome unknown"
        in diagnostic_context(db, later)["mailbox_tests"][setup.mailbox][0]["status"]
    )


def test_server_refusal_dns_and_late_revocation(
    db: Database, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DNS failures and a revoked owner do not transmit DATA; server errors are redacted."""
    job = approved_job(db, setup)
    monkeypatch.setattr(diagnostic_worker, "check_mail_route", lambda _: "temporary")
    assert "routing" in send_test(db, setup.config, job, clock=lambda: NOW)
    monkeypatch.setattr(diagnostic_worker, "check_mail_route", lambda _: "mx")

    def late_revoke(*args: object, before_data: Callable[[], bool]) -> SMTPResult:
        """Revoke the owner after authentication but before sending the body."""
        with db.sessions.begin() as session:
            actor = session.get(User, setup.actor)
            assert actor
            actor.active = False
        assert not before_data()
        return SMTPResult("cancelled", "policy_changed")

    monkeypatch.setattr(diagnostic_worker, "transmit", late_revoke)
    assert "not sent (policy_changed)" in send_test(db, setup.config, job, clock=lambda: NOW)


def test_web_form_requires_approval_owner_and_capable_worker(
    db: Database, setup: Setup, owner: Account, reviewer: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web requests only create signed jobs; no SMTP occurs in an HTTP request."""
    monkeypatch.setattr("app.api.delivery.utc_now", lambda: NOW)
    monkeypatch.setattr("app.api.mailbox_tests.utc_now", lambda: NOW)
    with TestClient(create_app(setup.config), follow_redirects=False) as client:
        sign_in(client, owner)
        page = client.get("/settings")
        assert "Test recipient" in page.text and SUBJECT in page.text
        assert "synthetic-only" not in page.text
        url = f"/settings/mailboxes/{setup.mailbox}/test"
        data = {
            "csrf_token": csrf_from(page.text),
            "request_id": new_id(),
            "recipient": "owner@example.com",
        }
        assert client.post(url, data=data).status_code == 422
        data["approve_test"] = "true"
        assert client.post(url, data={**data, "csrf_token": "wrong"}).status_code == 403
        assert client.post(url, data=data).status_code == 303
        assert client.post(url, data=data).status_code == 303
        with db.sessions() as session:
            assert len(list(session.scalars(select(Job).where(Job.kind == "mailbox_test")))) == 1
        sign_in(client, reviewer)
        assert client.post(url, data=data).status_code == 403
    with db.sessions.begin() as session:
        worker = session.get(WorkerInstance, "primary")
        assert worker
        worker.runtime_config = {"live_send_enabled": False}
    with pytest.raises(ValueError, match="updated PC worker"):
        request_test(
            db, setup.config, setup.mailbox, "owner@example.com", new_id(), setup.actor, NOW
        )
    assert not diagnostic_context(db, NOW)["mailbox_test_ready"]


def test_vercel_configuration_rejects_test_sender(settings: Settings) -> None:
    """Production web configuration cannot accidentally become a diagnostic sender."""
    with pytest.raises(ValidationError, match="MAILBOX_TESTS_ENABLED"):
        Settings.model_validate(
            {**settings.model_dump(), "app_mode": "VERCEL", "mailbox_tests_enabled": True}
        )


def test_uncertain_receipt_and_failed_job_preserve_no_retry(db: Database, setup: Setup) -> None:
    """An unexpected worker failure retains its durable marker and requires inbox review."""
    job = approved_job(db, setup)
    with pytest.raises(ValueError, match="actually received"):
        confirm_receipt(db, setup.mailbox, job.id, setup.actor)
    with db.sessions.begin() as session:
        stored = session.get(Job, job.id)
        assert stored
        stored.result = STARTED
    assert finish_job(
        db, job, NOW, error_code="temporary_failure", retry_at=NOW + timedelta(seconds=5)
    )
    with db.sessions() as session:
        stored = session.get(Job, job.id)
        assert stored and stored.state == "failed" and stored.result == STARTED
    with pytest.raises(ValueError, match="pending or uncertain"):
        request_test(
            db, setup.config, setup.mailbox, "owner@example.com", new_id(), setup.actor, NOW
        )
    confirm_receipt(db, setup.mailbox, job.id, setup.actor)
    context = diagnostic_context(db, NOW)["mailbox_tests"][setup.mailbox][0]
    assert context["status"].startswith("Received in the test inbox") and not context["can_confirm"]
