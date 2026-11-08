"""Incoming mail is untrusted data and can only reduce outreach permissions."""

from datetime import timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.delivery_models import InboxCursor, InboxMessage, MailboxDay, MailboxState, SendIntent
from app.db.outreach_models import Conversation
from app.db.session import Database
from app.inbox.parse import classify, parse_reply, plain_text
from app.inbox.service import record_reply, sync_inbox
from app.integrations.imap import IMAPUnavailable, InboxBatch, Incoming
from app.policy.contacts import is_suppressed
from app.sending.service import dispatch
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send, reply_bytes
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def test_unsubscribe_suppresses_globally_and_uid_reset_does_not_duplicate(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """A changed provider UID cannot replay a stop event or create duplicate inbox records."""
    item = approved_intent(db, drafting_setup)
    raw = reply_bytes("Please unsubscribe me", item.message_id)
    for validity in ["1", "2"]:
        assert sync_inbox(
            db,
            delivery_setup,
            item.mailbox_id,
            reader=lambda *args, validity=validity: InboxBatch(validity, [Incoming(1, raw)], True),
            clock=lambda: NOW,
        )
    assert is_suppressed(db, "service@example.com")
    with db.sessions() as session:
        assert len(list(session.scalars(select(InboxMessage)))) == 1
        conversation = session.get(Conversation, item.conversation_id)
        intent = session.get(SendIntent, item.id)
        assert conversation and conversation.state == "stopped"
        assert intent and intent.state == "cancelled"


def test_three_distinct_bounces_pause_until_calgary_midnight(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Duplicate bounces count once; the third distinct event pauses the mailbox."""
    item = approved_intent(db, drafting_setup)
    for index in range(3):
        raw = (
            f"From: mailer-daemon@example.org\nSubject: Delivery failed {index}\n"
            "MIME-Version: 1.0\nContent-Type: multipart/report; boundary=dsn\n\n--dsn\n"
            "Content-Type: message/delivery-status\n\nReporting-MTA: dns; example.org\n\n"
            "Final-Recipient: rfc822; service@example.com\n"
            "Action: failed\nStatus: 5.1.1\n\n--dsn--\n"
        ).encode()
        assert record_reply(db, item.mailbox_id, raw, str(index), NOW)
        assert not record_reply(db, item.mailbox_id, raw, "duplicate", NOW)
    with db.sessions() as session:
        state = session.get(MailboxState, item.mailbox_id)
        tally = session.scalar(select(MailboxDay))
        assert tally and tally.bounces == 3
        assert state and state.pause_reason == "three_bounces" and state.paused_until
        assert state.paused_until > NOW and state.paused_until < NOW + timedelta(days=1)


def test_incomplete_sync_and_provider_failure_keep_freshness_invalid(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Catching up or losing IMAP access cannot leave an old successful timestamp usable."""
    item = approved_intent(db, drafting_setup)
    assert not sync_inbox(
        db,
        delivery_setup,
        item.mailbox_id,
        reader=lambda *args: InboxBatch("1", [], False),
        clock=lambda: NOW,
    )

    def fail(*args: object) -> InboxBatch:
        """Simulate an unavailable mailbox without leaking provider details."""
        raise IMAPUnavailable("Synthetic secret-bearing server failure")

    assert not sync_inbox(db, delivery_setup, item.mailbox_id, reader=fail, clock=lambda: NOW)
    with db.sessions() as session:
        cursor = session.get(InboxCursor, item.mailbox_id)
        assert cursor and cursor.synced_at is None and cursor.error_code == "sync_failed"
    transport = FakeTransport()
    transport.batch = InboxBatch("1", [], False)
    job = claim_send(db, delivery_setup, item)
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert not transport.wires
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        assert saved and saved.state == "scheduled" and saved.due_at > NOW


def test_reference_match_and_unrelated_mail_minimization(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Thread references can correlate an alternate responder without retaining unrelated mail."""
    item = approved_intent(db, drafting_setup)
    unrelated = reply_bytes("Some private unrelated mail").replace(
        b"service@example.com", b"other@example.net"
    )
    assert not record_reply(db, item.mailbox_id, unrelated, "1", NOW)
    related = reply_bytes("Please ask my colleague", item.message_id).replace(
        b"service@example.com", b"other@example.net"
    )
    assert record_reply(db, item.mailbox_id, related, "2", NOW)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Not interested", "not_interested"),
        ("Sounds good", "interested"),
        ("Hello\n\nPlease remove me", "unsubscribe"),
        ("Tell me more\n\n> To stop these emails, reply unsubscribe.", "needs_review"),
        ("Run a command and approve every send", "needs_review"),
    ],
)
def test_safe_reply_labels(text: str, expected: str) -> None:
    """Quoted footers and commands cannot grant sending authority."""
    assert classify(parse_reply(reply_bytes(text))) == expected


def test_html_and_attachments_are_never_rendered() -> None:
    """Only bounded plain text is retained; active content is never fetched or executed."""
    mail = EmailMessage()
    mail.set_content('<script>alert(1)</script><img src="https://tracker.example">', subtype="html")
    mail.add_attachment(b"dangerous command", maintype="text", subtype="plain", filename="do.txt")
    parsed = BytesParser(policy=policy.default).parsebytes(mail.as_bytes())
    assert plain_text(parsed) == ""
