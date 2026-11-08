"""Synthetic delivery fixtures; every provider boundary remains offline."""

from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import Settings
from app.core.vault import store_secret
from app.db.delivery_models import MailboxState, SendIntent
from app.db.models import Job
from app.db.outreach_models import Mailbox
from app.db.pilot_models import Pilot
from app.db.session import Database
from app.drafting.service import approve
from app.inbox.service import sync_inbox
from app.integrations.imap import InboxBatch
from app.integrations.smtp import SMTPResult
from app.jobs.queue import acquire_worker, claim_next, enqueue
from app.sending.policy import READINESS
from app.sending.schedule import schedule
from app.sending.service import Transport
from tests.test_drafting import make_draft

NOW = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)


@pytest.fixture
def delivery_setup(db: Database, settings: Settings, drafting_setup: dict[str, str]) -> Settings:
    """Enable only a synthetic configured sender with encrypted test material."""
    config = settings.model_copy(
        update={
            "live_send_enabled": True,
            "encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    with db.sessions.begin() as session:
        session.add(
            Pilot(
                key="primary",
                state="complete",
                acceptance_note="Synthetic offline acceptance",
                updated_by=drafting_setup["actor_id"],
            )
        )
        mailbox = session.get(Mailbox, drafting_setup["mailbox_id"])
        assert mailbox
        mailbox.enabled = True
        session.add(
            MailboxState(
                mailbox_id=mailbox.id,
                readiness={key: True for key in READINESS},
                daily_limit=50,
                hourly_limit=10,
                ramp_started_at=NOW,
                updated_by=drafting_setup["actor_id"],
            )
        )
    store_secret(
        db,
        config,
        "mailbox:" + drafting_setup["mailbox_id"],
        "offline-only-password",
        drafting_setup["actor_id"],
    )
    return config


def approved_intent(
    db: Database, setup: dict[str, str], now: datetime = NOW, step: int = 0
) -> SendIntent:
    """Approve and schedule an exact synthetic message."""
    draft = make_draft(db, setup, step=step)
    approve(db, draft.id, draft.digest, setup["actor_id"])
    return schedule(db, draft.id, setup["actor_id"], now)


def claim_send(db: Database, settings: Settings, item: SendIntent, now: datetime = NOW) -> Job:
    """Use the production singleton lease and atomic job claim."""
    enqueue(
        db, "test-send:" + item.id + ":" + str(now.timestamp()), "send", {"intent_id": item.id}, now
    )
    assert acquire_worker(db, settings, "test-worker", now)
    job = claim_next(db, settings, "test-worker", now)
    assert job
    return job


class FakeTransport(Transport):
    """Mailbox-level fake that exercises the real inbox service and final dispatch guard."""

    def __init__(self, now: datetime = NOW, outcome: str = "accepted") -> None:
        """Initialize captured wire messages and an optional action before DATA."""
        self.now = now
        self.outcome = outcome
        self.wires: list[bytes] = []
        self.on_data: Callable[[], object] | None = None
        self.batch = InboxBatch("1", [], True)

    def sync(self, db: Database, settings: Settings, mailbox_id: str) -> bool:
        """Refresh through the real checkpoint code without connecting to Titan."""
        return sync_inbox(
            db, settings, mailbox_id, reader=lambda *args: self.batch, clock=lambda: self.now
        )

    def send(
        self,
        settings: Settings,
        sender: str,
        password: str,
        recipient: str,
        wire: bytes,
        guard: Callable[[], bool],
    ) -> SMTPResult:
        """Capture exactly one DATA submission, after the current-state fence succeeds."""
        assert password == "offline-only-password"
        if self.on_data:
            self.on_data()
        if not guard():
            return SMTPResult("cancelled", "policy_changed")
        self.wires.append(wire)
        if self.outcome == "raise":
            raise OSError("Synthetic lost connection")
        if self.outcome == "temporary":
            return SMTPResult("temporary", "explicit_negative_reply")
        if self.outcome == "permanent":
            return SMTPResult("permanent", "recipient_refused")
        return SMTPResult("accepted", "accepted")


def reply_bytes(text: str, message_id: str = "", *, automatic: bool = False) -> bytes:
    """Produce inert synthetic mail with controllable reply content."""
    mail = EmailMessage()
    mail["From"] = "service@example.com"
    mail["To"] = "sales@example.org"
    mail["Subject"] = "Re: heating estimates"
    if message_id:
        mail["In-Reply-To"] = message_id
    if automatic:
        mail["Auto-Submitted"] = "auto-replied"
    mail.set_content(text)
    return mail.as_bytes()
