"""Persist mailbox evidence and stop outreach before interpreting incoming content."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import InboxCursor, InboxMessage, MailboxDay, MailboxState, SendIntent
from app.db.lead_models import Contact, SuppressionEntry
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.session import Database
from app.inbox.parse import classify, parse_reply
from app.integrations.imap import IMAPUnavailable, InboxBatch, read_inbox
from app.pipeline.service import capture_reply
from app.sending.window import CALGARY, end_of_day

InboxReader = Callable[[Settings, str, str, str, int], InboxBatch]


def record_reply(
    db: Database, mailbox_id: str, raw: bytes, provider_key: str, now: datetime
) -> bool:
    """Deduplicate, cancel pending work and then label a known conversation's reply."""
    reply = parse_reply(raw)
    try:
        with db.sessions.begin() as session:
            if session.scalar(
                select(InboxMessage.id).where(
                    InboxMessage.mailbox_id == mailbox_id,
                    InboxMessage.dedupe_key == reply.dedupe_key,
                )
            ):
                return False
            conversation = session.scalar(
                select(Conversation)
                .join(Contact)
                .where(
                    Conversation.mailbox_id == mailbox_id,
                    Contact.email.in_(
                        [reply.sender, reply.bounce_recipient, reply.delivery_recipient]
                    ),
                )
            )
            if conversation is None and reply.references:
                conversation = session.scalar(
                    select(Conversation)
                    .join(SendIntent)
                    .where(
                        SendIntent.mailbox_id == mailbox_id,
                        SendIntent.message_id.in_(reply.references),
                    )
                )
            # Do not retain unrelated personal mail or treat it as instructions.
            if conversation is None:
                return False
            label = classify(reply)
            if label in {"delivery_delayed", "delivery_notice"}:
                session.add(
                    InboxMessage(
                        mailbox_id=mailbox_id,
                        conversation_id=conversation.id,
                        provider_key=provider_key[:80],
                        dedupe_key=reply.dedupe_key,
                        sender=reply.sender,
                        subject=reply.subject,
                        body=reply.text,
                        classification=label,
                        received_at=now,
                    )
                )
                return True
            conversation.state = "replied"
            pending = list(
                session.scalars(
                    select(SendIntent).where(
                        SendIntent.conversation_id == conversation.id,
                        SendIntent.state.in_(["scheduled", "transmitting"]),
                    )
                )
            )
            for item in pending:
                item.state, item.hold_reason = "cancelled", "reply_received"
            session.execute(
                update(Draft)
                .where(
                    Draft.conversation_id == conversation.id,
                    Draft.state.in_(["draft", "approved", "sending"]),
                )
                .values(state="cancelled")
            )
            # Classification cannot authorize sending; cancellation happens first.
            session.flush()
            label = classify(reply)
            capture_reply(session, conversation, label, now)
            if label in {"automatic", "needs_review", "bounce"}:
                conversation.state = "held"
            contact = session.get(Contact, conversation.contact_id)
            if label in {"unsubscribe", "not_interested"} and contact:
                key = "email:" + contact.email
                if not session.scalar(
                    select(SuppressionEntry.id).where(SuppressionEntry.key == key)
                ):
                    session.add(
                        SuppressionEntry(key=key, reason="Recipient requested outreach stop")
                    )
                conversation.state = "stopped"
            session.add(
                InboxMessage(
                    mailbox_id=mailbox_id,
                    conversation_id=conversation.id,
                    provider_key=provider_key[:80],
                    dedupe_key=reply.dedupe_key,
                    sender=reply.sender,
                    subject=reply.subject,
                    body=reply.text,
                    classification=label,
                    received_at=now,
                )
            )
            if label == "bounce":
                _bounce(session, mailbox_id, now)
            return True
    except IntegrityError:
        with db.sessions() as session:
            if session.scalar(
                select(InboxMessage.id).where(
                    InboxMessage.mailbox_id == mailbox_id,
                    InboxMessage.dedupe_key == reply.dedupe_key,
                )
            ):
                return False
        raise


def _bounce(session: Session, mailbox_id: str, now: datetime) -> None:
    day = now.astimezone(CALGARY).date().isoformat()
    tally = session.scalar(
        select(MailboxDay).where(MailboxDay.mailbox_id == mailbox_id, MailboxDay.local_day == day)
    )
    if tally is None:
        tally = MailboxDay(mailbox_id=mailbox_id, local_day=day, bounces=0, reserved=0)
        session.add(tally)
    tally.bounces += 1
    state = session.get(MailboxState, mailbox_id)
    if state and tally.bounces >= 3:
        state.paused_until, state.pause_reason = end_of_day(now), "three_bounces"


def sync_inbox(
    db: Database,
    settings: Settings,
    mailbox_id: str,
    *,
    reader: InboxReader = read_inbox,
    clock: Callable[[], datetime] = utc_now,
) -> bool:
    """Invalidate freshness first; only a complete successful read can restore it."""
    with db.sessions.begin() as session:
        mailbox = session.get(Mailbox, mailbox_id)
        if mailbox is None or mailbox.provider not in {"titan", "gmail"}:
            raise ValueError("Choose an existing Titan or Gmail mailbox")
        cursor = session.get(InboxCursor, mailbox_id)
        if cursor is None:
            cursor = InboxCursor(mailbox_id=mailbox_id, uidvalidity="", last_uid=0)
            session.add(cursor)
        validity, checkpoint, email = cursor.uidvalidity, cursor.last_uid, mailbox.email
        cursor.synced_at, cursor.error_code = None, "sync_in_progress"
    try:
        password = read_secret(db, settings, "mailbox:" + mailbox_id)
        provider_settings = settings.model_copy(update={"mail_provider": mailbox.provider})
        batch = reader(provider_settings, email, password, validity, checkpoint)
        for incoming in batch.messages:
            record_reply(
                db, mailbox_id, incoming.raw, f"{batch.uidvalidity}:{incoming.uid}", clock()
            )
    except (IMAPUnavailable, ValueError):
        with db.sessions.begin() as session:
            cursor = session.get(InboxCursor, mailbox_id)
            assert cursor
            cursor.error_code = "sync_failed"
        return False
    with db.sessions.begin() as session:
        cursor = session.get(InboxCursor, mailbox_id)
        assert cursor
        base = checkpoint if validity == batch.uidvalidity else 0
        cursor.last_uid = max([base, *(item.uid for item in batch.messages)])
        cursor.uidvalidity = batch.uidvalidity
        cursor.synced_at = clock() if batch.complete else None
        cursor.error_code = None if batch.complete else "catching_up"
    return batch.complete
