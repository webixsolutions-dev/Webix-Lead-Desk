"""Regression coverage for the owner's outreach review; no live service calls."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import Mock, patch

import dns.resolver
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.ai_config import task_settings
from app.core.config import Settings
from app.db.delivery_models import InboxMessage, MailboxDay, SendIntent
from app.db.outreach_models import Conversation, Mailbox
from app.db.session import Database
from app.drafting.content import check_copy
from app.drafting.service import approve
from app.inbox.parse import classify, parse_reply
from app.inbox.service import record_reply
from app.integrations.dns_mail import check_mail_route
from app.sending.schedule import schedule
from app.sending.service import dispatch
from app.ui.preview import NAVIGATION
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send, reply_bytes
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


@pytest.mark.parametrize(
    "body",
    [
        "I'm not really interested right now",
        "We aren't interested",
        "I’m not particularly interested",
        "No thanks, sounds good for someone else",
    ],
)
def test_negative_reply_never_becomes_interested(body: str) -> None:
    """Negative intent takes precedence over positive keywords."""
    assert classify(parse_reply(reply_bytes(body))) == "not_interested"


def test_delay_is_not_a_bounce_or_prospect_reply(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Repeated delayed DSNs hold follow-ups without consuming the bounce budget."""
    item = approved_intent(db, drafting_setup)
    for n in range(3):
        raw = (
            f"From: daemon@example.org\nSubject: Delay {n}\n"
            "MIME-Version: 1.0\nContent-Type: multipart/report; boundary=x\n\n--x\n"
            "Content-Type: message/delivery-status\n\nReporting-MTA: dns; example.org\n\n"
            "Final-Recipient: rfc822; service@example.com\nAction: delayed\n"
            "Status: 4.2.0\n\n--x--\n"
        ).encode()
        assert record_reply(db, item.mailbox_id, raw, str(n), NOW)
    with db.sessions() as session:
        assert session.get(Conversation, item.conversation_id).state == "held"  # type: ignore[union-attr]
        assert session.get(SendIntent, item.id).state == "held"  # type: ignore[union-attr]
        assert session.scalar(select(MailboxDay)) is None
        assert {m.classification for m in session.scalars(select(InboxMessage))} == {
            "delivery_delayed"
        }


@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("body", ["We can build this for $500.", "This costs CAD 500 today."])
def test_prices_blocked_at_all_steps(first: bool, body: str) -> None:
    """Follow-ups use the same price rule as first contact."""
    with pytest.raises(ValueError, match="prices"):
        check_copy("A useful suggestion", body, first_touch=first)


@pytest.mark.parametrize("link", ["https://example.com", "www.example.com", "example.com/demo"])
def test_first_touch_links_blocked(link: str) -> None:
    """Plain domains and explicit URLs are both blocked in initial copy."""
    with pytest.raises(ValueError, match="links"):
        check_copy("A useful suggestion", "Please visit " + link)
    check_copy("A useful suggestion", "Please visit " + link, first_touch=False)


def test_gmail_profile_and_workflow_tabs(client: TestClient, owner: Account, db: Database) -> None:
    """The authenticated form accepts Gmail without turning on sending."""
    sign_in(client, owner)
    page = client.get("/settings")
    result = client.post(
        "/settings/mailboxes",
        data={
            "csrf_token": csrf_from(page.text),
            "email": "synthetic@gmail.com",
            "provider": "gmail",
            "lane": "client",
            "brand": "Example",
            "sender_name": "Example Owner",
            "mailing_address": "1 Example Street",
            "contact_details": "synthetic@gmail.com",
        },
    )
    assert result.status_code == 303
    with db.sessions() as session:
        box = session.scalar(select(Mailbox).where(Mailbox.email == "synthetic@gmail.com"))
        assert box and box.provider == "gmail" and not box.enabled
    assert {"/bookings", "/linkedin/activity"}.issubset({item[1] for item in NAVIGATION})
    assert all(path in page.text for path in ['href="#learning"', 'href="#operations"'])


def test_four_followups_remain_in_same_thread_then_reply_stops(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """All four separately approved steps obey spacing, sender and reply cancellation."""
    now = NOW
    item = approved_intent(db, drafting_setup)
    original_sender = item.mailbox_id
    first_message = item.message_id
    for step in range(5):

        def clock(now: datetime = now) -> datetime:
            """Freeze this iteration clock."""
            return now

        transport = FakeTransport(now)
        job = claim_send(db, delivery_setup, item, now)
        assert (
            dispatch(db, delivery_setup, job, transport=transport, clock=clock)
            == "Delivery result: accepted."
        )
        assert len(transport.wires) == 1
        if step == 4:
            break
        draft = make_draft(db, drafting_setup, step=step + 1)
        assert draft.snapshot["in_reply_to"] == item.message_id
        assert draft.snapshot["references"] == [first_message]
        approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
        item = schedule(db, draft.id, drafting_setup["actor_id"], now)
        assert item.mailbox_id == original_sender and item.due_at >= now + timedelta(days=3)
        now = item.due_at
    with pytest.raises(ValueError, match="four follow-ups"):
        make_draft(db, drafting_setup, step=5)
    record_reply(db, item.mailbox_id, reply_bytes("Not really interested"), "reply", now)
    with pytest.raises(ValueError):
        make_draft(db, drafting_setup, step=4)


def test_mx_null_fallback_and_timeout() -> None:
    """DNS only establishes mail routing; absent/temporary routing cannot pass as valid."""
    with patch("app.integrations.dns_mail.dns.resolver.Resolver") as factory:
        resolver = factory.return_value
        resolver.resolve.return_value = [Mock(exchange=".")]
        assert check_mail_route("hello@example.com") == "no_mail"
        resolver.resolve.return_value = [Mock(exchange="mx.example.com.")]
        assert check_mail_route("hello@example.com") == "mx"
        resolver.resolve.side_effect = [dns.resolver.NoAnswer(), [Mock()]]  # type: ignore[no-untyped-call]
        assert check_mail_route("hello@example.com") == "implicit_mx"
        resolver.resolve.side_effect = dns.resolver.LifetimeTimeout()  # type: ignore[no-untyped-call]
        assert check_mail_route("hello@example.com") == "temporary"


def test_failed_dns_never_opens_smtp(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """DNS failure holds the message before an SMTP attempt is reserved."""
    item = approved_intent(db, drafting_setup)
    transport = FakeTransport()
    with patch.object(transport, "mail_route", return_value="no_mail"):
        dispatch(
            db,
            delivery_setup,
            claim_send(db, delivery_setup, item),
            transport=transport,
            clock=lambda: NOW,
        )
    assert not transport.wires
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        assert saved and saved.state == "held" and saved.attempts == 0


def test_task_models_cannot_inherit_wrong_prices(settings: Settings) -> None:
    """Distinct models require their own verified prices, without legacy leakage."""
    values: dict[str, Any] = {
        "openai_audit_model": "gpt-6-luna",
        "openai_draft_model": "gpt-6-sol",
        "openai_input_usd_micro_per_million": 999,
    }
    config = settings.model_copy(update=values)
    assert task_settings(config, "audit").openai_model == "gpt-6-luna"
    assert task_settings(config, "draft").openai_model == "gpt-6-sol"
    assert task_settings(config, "audit").openai_input_usd_micro_per_million == 0
