"""Real aggregates, timezone boundaries and bounded desktop refresh behavior."""

from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage, SendAttempt, SendIntent
from app.db.lead_models import Campaign, Company, Contact
from app.db.models import WorkspaceSetting
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.db.session import Database
from app.desktop import analytics
from app.desktop.metrics import read_snapshot
from tests.conftest import Account

NOW = datetime(2026, 11, 13, 1, tzinfo=UTC)


def test_counts_real_businesses_and_local_days(
    db: Database, settings: Settings, owner: Account
) -> None:
    """Retries, follow-ups, robot mail and reference-matched strangers never inflate results."""
    with db.sessions.begin() as session:
        campaign = Campaign(name="Synthetic", industry="IT", area="Example", services=[])
        mailbox = Mailbox(
            email="owner@example.test",
            lane="client",
            brand="Example",
            sender_name="Owner",
            mailing_address="Example",
            contact_details="Example",
        )
        session.add_all([campaign, mailbox])
        session.flush()
        conversations = []
        contacts = []
        for i in range(4):
            company = Company(
                identity_key=str(i), name=f"Business {i}", city="Example", industry="IT"
            )
            session.add(company)
            session.flush()
            contact = Contact(company_id=company.id, email=f"contact{i}@example.test")
            session.add(contact)
            session.flush()
            conversation = Conversation(
                company_id=company.id,
                campaign_id=campaign.id,
                contact_id=contact.id,
                mailbox_id=mailbox.id,
            )
            session.add(conversation)
            session.flush()
            conversations.append(conversation)
            contacts.append(contact)
        for i, (which, step, state, at) in enumerate(
            (
                (0, 0, "accepted", datetime(2026, 11, 12, 18, 59, tzinfo=UTC)),
                (0, 1, "accepted", NOW - timedelta(hours=5)),
                (1, 0, "accepted", NOW - timedelta(days=40)),
                (2, 0, "scheduled", None),
                (3, 0, "accepted", NOW - timedelta(minutes=20)),
                (0, 2, "held", None),
                (0, 3, "uncertain", None),
            )
        ):
            draft = Draft(
                conversation_id=conversations[which].id,
                step=step,
                revision=1,
                snapshot={},
                digest=str(i),
                state="sent" if state == "accepted" else "approved",
                created_by=owner.user.id,
            )
            session.add(draft)
            session.flush()
            intent = SendIntent(
                conversation_id=conversations[which].id,
                draft_id=draft.id,
                mailbox_id=mailbox.id,
                step=step,
                state=state,
                due_at=NOW,
                message_id=f"{i}@example.test",
                approval_digest=draft.digest,
                accepted_at=at,
            )
            session.add(intent)
            session.flush()
            if i == 0:
                for _ in range(3):
                    session.add(
                        SendAttempt(
                            intent_id=intent.id,
                            mailbox_id=mailbox.id,
                            outcome="failed",
                            started_at=NOW,
                        )
                    )
        for i, (which, classification, sender, at) in enumerate(
            (
                (0, "interested", contacts[0].email, NOW),
                (0, "needs_review", contacts[0].email.upper(), NOW),
                (1, "not_interested", contacts[1].email, NOW),
                (2, "interested", contacts[2].email, NOW),  # no accepted send
                (3, "automatic", contacts[3].email, NOW),
                (3, "bounce", contacts[3].email, NOW),
                (3, "delivery_delayed", contacts[3].email, NOW),
                (3, "delivery_notice", contacts[3].email, NOW),
                (3, "needs_review", "stranger@example.test", NOW),
                (3, "interested", contacts[3].email, NOW - timedelta(days=1)),  # before send
                (3, "interested", contacts[3].email, NOW + timedelta(days=1)),  # future
            )
        ):
            session.add(
                InboxMessage(
                    mailbox_id=mailbox.id,
                    conversation_id=conversations[which].id,
                    provider_key=str(i),
                    dedupe_key=str(i),
                    sender=sender,
                    subject="Synthetic",
                    body="Private synthetic content",
                    classification=classification,
                    received_at=at,
                )
            )
        for i, state in enumerate(("draft", "superseded", "cancelled", "draft")):
            session.add(
                Draft(
                    conversation_id=conversations[1].id,
                    step=i + 1,
                    revision=1,
                    snapshot={},
                    digest=f"pending{i}",
                    state=state,
                    created_by=owner.user.id,
                )
            )
        for i, (state, days) in enumerate(
            (
                ("confirmed", 1),
                ("proposed", 2),
                ("cancelled", 3),
                ("confirmed", -1),
                ("confirmed", 10),
            )
        ):
            session.add(
                Booking(
                    company_id=conversations[0].company_id,
                    title=f"Meeting {i}",
                    contact="Example",
                    host="Example",
                    starts_at=NOW + timedelta(days=days),
                    ends_at=NOW + timedelta(days=days, hours=1),
                    timezone="Asia/Karachi",
                    state=state,
                    created_by=owner.user.id,
                )
            )
    today = read_snapshot(db, settings, "Today", NOW)
    assert (today.contacted, today.sent, today.replied) == (2, 2, 2)
    assert (today.approvals, today.scheduled, today.attention, today.meetings) == (2, 1, 2, 1)
    assert today.days[-1].sent == 2 and today.days[-2].sent == 1
    assert today.days[-1].replied == 2
    week = read_snapshot(db, settings, "Last 7 days", NOW)
    assert (week.contacted, week.sent) == (2, 3)
    total = read_snapshot(db, settings, "All time", NOW)
    assert (total.contacted, total.sent, total.replied) == (3, 4, 2)
    # Aggregates cannot mutate delivery, draft or conversation state.
    with db.sessions() as session:
        assert len(list(session.scalars(select(SendAttempt)))) == 3
        assert (
            len(list(session.scalars(select(SendIntent).where(SendIntent.state == "accepted"))))
            == 4
        )
    assert "Private synthetic content" not in repr(total)
    with db.sessions.begin() as session:
        session.add(
            WorkspaceSetting(
                key="workspace",
                version=1,
                value={
                    "workspace_name": "Synthetic",
                    "display_timezone": "UTC",
                    "sending_timezone": "America/Edmonton",
                    "preview_enabled": False,
                },
            )
        )
    utc_today = read_snapshot(db, settings, "Today", NOW)
    assert utc_today.timezone == "UTC" and utc_today.sent == 1
    assert utc_today.days[-2].sent == 2


def test_empty_snapshot_and_invalid_time(db: Database, settings: Settings) -> None:
    """Connected zero activity is a real zero, not an unavailable snapshot."""
    value = read_snapshot(db, settings, "Last 30 days", NOW)
    assert value.sent == value.replied == value.contacted == value.approvals == 0
    assert len(value.days) == 7 and all(day.sent == 0 for day in value.days)
    with pytest.raises(ValueError):
        read_snapshot(db, settings, "Today", NOW.replace(tzinfo=None))


def test_refresh_serializes_and_redacts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure returns one safe result and never retries or starts overlapping reads."""
    entered, release = Event(), Event()
    database = Mock()
    monkeypatch.setattr(analytics, "create_database", lambda _: database)

    def fail(*args: object) -> None:
        entered.set()
        assert release.wait(3)
        raise RuntimeError("PRIVATE_DATABASE_PASSWORD")

    reader = Mock(side_effect=fail)
    monkeypatch.setattr(analytics, "read_snapshot", reader)
    controller = analytics.AnalyticsController()
    assert controller.refresh(settings, "Today", 7)
    assert entered.wait(3)
    assert not controller.refresh(settings, "All time", 8)
    release.set()
    assert controller.thread is not None
    controller.thread.join(3)
    result = controller.results.get_nowait()
    assert result.generation == 7 and result.snapshot is None
    assert "PRIVATE_DATABASE_PASSWORD" not in repr(result)
    assert reader.call_count == 1 and not controller.active and controller.results.empty()
    database.engine.dispose.assert_called_once()
