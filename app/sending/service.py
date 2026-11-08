"""One SMTP attempt per reservation, with conservative uncertain-outcome reconciliation."""

from collections.abc import Callable
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import SendAttempt, SendIntent
from app.db.models import Job
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.session import Database
from app.inbox.parse import plain_text
from app.inbox.service import sync_inbox
from app.integrations.imap import find_sent
from app.integrations.smtp import SMTPResult, transmit
from app.sending.reservations import before_data, reserve
from app.sending.window import next_window


class Transport:
    """Replaceable external boundaries for offline failure testing."""

    def sync(self, db: Database, settings: Settings, mailbox_id: str) -> bool:
        """Read all new inbox mail before any delivery attempt."""
        return sync_inbox(db, settings, mailbox_id)

    def send(
        self,
        settings: Settings,
        sender: str,
        password: str,
        recipient: str,
        wire: bytes,
        guard: Callable[[], bool],
    ) -> SMTPResult:
        """Cross the single SMTP adapter boundary."""
        return transmit(settings, sender, password, recipient, wire, before_data=guard)


def dispatch(
    db: Database,
    settings: Settings,
    job: Job,
    *,
    transport: Transport | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> str:
    """Sync, reserve and send once; exceptions after reservation never trigger a blind retry."""
    if not settings.live_send_enabled or settings.app_mode == "VERCEL":
        return "Delivery is disabled on this process."
    transport = transport or Transport()
    item_id = str(job.payload["intent_id"])
    with db.sessions() as session:
        item = session.get(SendIntent, item_id)
        if not item or item.state != "scheduled":
            return "No scheduled transmission remains."
        mailbox_id = item.mailbox_id
    if not transport.sync(db, settings, mailbox_id):
        from app.sending.reservations import postpone

        postpone(db, item_id, clock() + timedelta(minutes=5), "inbox_sync_required")
        return "Inbox sync is incomplete; sending is deferred."
    password = read_secret(db, settings, "mailbox:" + mailbox_id)
    attempt = reserve(db, settings, job, item_id, clock())
    if not attempt:
        return "Delivery was deferred or held by current policy."
    with db.sessions() as session:
        item = session.get(SendIntent, item_id)
        assert item and item.wire_message
        draft = session.get(Draft, item.draft_id)
        assert draft
        snapshot, wire = draft.snapshot, item.wire_message.encode()
    try:
        result = transport.send(
            settings,
            snapshot["from_email"],
            password,
            snapshot["recipient"],
            wire,
            lambda: before_data(db, settings, job, item_id, clock()),
        )
    except Exception:
        # Even an unexpected adapter failure is ambiguous once the durable reservation exists.
        result = SMTPResult("uncertain", "unexpected_transport_failure")
    record_result(db, attempt.id, result, clock())
    return f"Delivery result: {result.outcome}."


def record_result(db: Database, attempt_id: str, result: SMTPResult, now: datetime) -> None:
    """Persist known acceptance without reviving a conversation that has already replied."""
    with db.sessions.begin() as session:
        attempt = session.get(SendAttempt, attempt_id)
        assert attempt
        item = session.get(SendIntent, attempt.intent_id)
        assert item
        conversation = session.get(Conversation, item.conversation_id)
        draft = session.get(Draft, item.draft_id)
        assert conversation and draft
        if attempt.outcome not in {"started", "uncertain"} or item.state == "accepted":
            return
        attempt.outcome, attempt.error_code, attempt.finished_at = result.outcome, result.code, now
        item.hold_reason = result.code
        if result.outcome == "accepted":
            item.state, item.accepted_at = "accepted", now
            draft.state = "sent"
            from app.demos.service import record_demo_acceptance

            record_demo_acceptance(session, draft)
            conversation.first_message_id = conversation.first_message_id or item.message_id
            conversation.last_message_id = item.message_id
            if conversation.state == "draft":
                conversation.state = "active"
        elif result.outcome == "uncertain":
            item.state, draft.state = "uncertain", "uncertain"
            if conversation.state in {"draft", "active"}:
                conversation.state = "held"
        elif (
            result.outcome == "temporary"
            and item.attempts < 3
            and conversation.state in {"draft", "active"}
            and item.state != "cancelled"
        ):
            item.state, draft.state = "scheduled", "approved"
            item.due_at = next_window(now + timedelta(minutes=5 * item.attempts))
        else:
            item.state, draft.state = "cancelled", "cancelled"


def reconcile(
    db: Database,
    settings: Settings,
    item_id: str,
    *,
    finder: Callable[[Settings, str, str, str], list[bytes]] = find_sent,
) -> bool:
    """Confirm exact provider Sent evidence; absence leaves the uncertain send permanently held."""
    with db.sessions() as session:
        item = session.get(SendIntent, item_id)
        if not item or item.state != "uncertain" or not item.wire_message:
            raise ValueError("Only an uncertain reserved message can be reconciled")
        mailbox = session.get(Mailbox, item.mailbox_id)
        attempt = session.scalar(
            select(SendAttempt)
            .where(SendAttempt.intent_id == item.id)
            .order_by(SendAttempt.started_at.desc())
            .limit(1)
        )
        assert mailbox and attempt
    password = read_secret(db, settings, "mailbox:" + mailbox.id)
    expected = BytesParser(policy=policy.default).parsebytes(item.wire_message.encode())
    for raw in finder(settings, mailbox.email, password, item.message_id):
        found = BytesParser(policy=policy.default).parsebytes(raw)
        if (
            str(found.get("Message-ID", "")) == item.message_id
            and all(
                parseaddr(str(found.get(key, "")))[1].casefold()
                == parseaddr(str(expected[key]))[1].casefold()
                for key in ["From", "To"]
            )
            and str(found.get("Subject", "")) == str(expected["Subject"])
            and plain_text(found).strip() == plain_text(expected).strip()
        ):
            record_result(db, attempt.id, SMTPResult("accepted", "sent_copy_confirmed"), utc_now())
            # A human must review the held conversation before any further outreach.
            return True
    return False
