"""Provider-independent sender locks, sequence cancellation and rate-limit regressions."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.delivery_models import MailboxDay, MailboxState, SendAttempt, SendIntent
from app.db.lead_models import Campaign
from app.db.outreach_models import Draft, Mailbox
from app.db.session import Database
from app.drafting.service import approve
from app.inbox.parse import classify, parse_reply
from app.inbox.service import record_reply
from app.integrations.smtp import transmit
from app.jobs.queue import finish_job
from app.sending.providers import PRESETS, connection, mailbox_settings, readiness_fields
from app.sending.schedule import schedule
from app.sending.sequences import earliest_followup, validate_days
from app.sending.service import dispatch, reconcile
from app.sources.campaigns import edit_campaign
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send, reply_bytes
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import BODY, make_draft
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def select_provider(db: Database, setup: dict[str, str], provider: str) -> None:
    """Configure an offline mailbox before creating an exact approved snapshot."""
    with db.sessions.begin() as session:
        box = session.get(Mailbox, setup["mailbox_id"])
        assert box
        box.provider = provider
        box.email = "synthetic@gmail.com" if provider == "gmail" else "sales@example.org"
        state = session.get(MailboxState, box.id)
        assert state
        state.readiness = {field: True for field in readiness_fields(box)}


def accept(db: Database, config: Settings, item: SendIntent, now: datetime) -> None:
    """Transmit to the fake boundary and release the claimed worker job."""
    transport = FakeTransport(now)
    job = claim_send(db, config, item, now)
    assert dispatch(db, config, job, transport=transport, clock=lambda: now).endswith("accepted.")
    assert len(transport.wires) == 1
    finish_job(db, job, now, result="offline acceptance")


@pytest.mark.parametrize("provider", ["titan", "hostinger", "gmail"])
@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_reply_cancels_at_every_sequence_step(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], provider: str, step: int
) -> None:
    """Every provider cancels held and scheduled outreach at each follow-up position."""
    select_provider(db, drafting_setup, provider)
    item = approved_intent(db, drafting_setup)
    accept(db, delivery_setup, item, NOW)
    for index in range(1, step + 1):
        draft = make_draft(db, drafting_setup, step=index)
        approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
        item = schedule(db, draft.id, drafting_setup["actor_id"], NOW)
        if index < step:
            accept(db, delivery_setup, item, item.due_at)
    with db.sessions.begin() as session:
        saved = session.get(SendIntent, item.id)
        assert saved
        saved.state = "held"
    assert record_reply(
        db, item.mailbox_id, reply_bytes("Sounds good", item.message_id), "reply", NOW
    )
    with db.sessions() as session:
        assert not list(
            session.scalars(
                select(SendIntent).where(
                    SendIntent.state.in_(["scheduled", "held", "transmitting"])
                )
            )
        )
        saved_draft = session.get(Draft, item.draft_id)
        assert saved_draft and saved_draft.state == "cancelled"


@pytest.mark.parametrize("provider", ["titan", "hostinger", "gmail"])
def test_plain_bounce_and_uncertain_reconciliation_all_providers(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], provider: str
) -> None:
    """Body Message-ID correlation counts failures and never retries an uncertain transaction."""
    select_provider(db, drafting_setup, provider)
    item = approved_intent(db, drafting_setup)
    transport = FakeTransport(outcome="raise")
    job = claim_send(db, delivery_setup, item)
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert len(transport.wires) == 1
    assert not reconcile(db, delivery_setup, item.id, finder=lambda *args: [])
    assert reconcile(db, delivery_setup, item.id, finder=lambda *args: transport.wires)
    for index in range(3):
        raw = (
            f"From: mailer-daemon@example.org\nSubject: failed {index}\n\n"
            f"Permanent delivery failure: 5.1.1 user unknown.\nMessage-ID: {item.message_id}\n"
        ).encode()
        assert classify(parse_reply(raw)) == "bounce"
        assert record_reply(db, item.mailbox_id, raw, str(index), NOW)
    with db.sessions() as session:
        day = session.scalar(select(MailboxDay))
        state = session.get(MailboxState, item.mailbox_id)
        assert day and day.bounces == 3
        assert state and state.pause_reason == "three_bounces"


@pytest.mark.parametrize("provider", ["gmail", "hostinger"])
def test_titan_locked_lead_cannot_change_provider(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], provider: str
) -> None:
    """Changing to another valid mailbox does not evade the business sender lock."""
    item = approved_intent(db, drafting_setup)
    accept(db, delivery_setup, item, NOW)
    with db.sessions.begin() as session:
        box = Mailbox(
            email="other@example.org",
            provider=provider,
            lane="client",
            brand="Synthetic",
            sender_name="Example",
            mailing_address="Example address",
            contact_details="other@example.org",
        )
        session.add(box)
        session.flush()
        other_id = box.id
    with pytest.raises(ValueError, match="locked"):
        make_draft(db, drafting_setup, step=1, mailbox_id=other_id)


@pytest.mark.parametrize("lane,days", [("client", [3, 7, 12, 20]), ("partner", [4, 10])])
def test_offsets_and_minimum_actual_spacing(
    db: Database,
    delivery_setup: Settings,
    drafting_setup: dict[str, str],
    lane: str,
    days: list[int],
) -> None:
    """Offsets start at accepted first contact; late steps retain two-day spacing."""
    first = approved_intent(db, drafting_setup)
    accept(db, delivery_setup, first, NOW)
    with db.sessions.begin() as session:
        campaign = session.get(Campaign, drafting_setup["campaign_id"])
        assert campaign
        campaign.lane, campaign.followup_days = lane, days
        for seq, offset in enumerate(days, 1):
            probe = Draft(
                conversation_id=first.conversation_id,
                kind="follow_up",
                sequence_step=seq,
                snapshot={"campaign_id": campaign.id},
            )
            # Predecessor rows deliberately simulate a late prior acceptance.
            if seq > 1:
                previous = Draft(
                    conversation_id=first.conversation_id,
                    kind="follow_up",
                    sequence_step=seq - 1,
                    step=seq - 1,
                    revision=1,
                    snapshot={},
                    digest="x" * 64,
                    state="sent",
                    created_by=drafting_setup["actor_id"],
                )
                session.add(previous)
                session.flush()
                session.add(
                    SendIntent(
                        conversation_id=first.conversation_id,
                        draft_id=previous.id,
                        mailbox_id=first.mailbox_id,
                        step=seq - 1,
                        due_at=NOW,
                        message_id=f"<{seq}@example.org>",
                        approval_digest=previous.digest,
                        state="accepted",
                        accepted_at=NOW + timedelta(days=offset - 1),
                    )
                )
                session.flush()
            due = earliest_followup(session, probe)
            assert due == NOW + timedelta(days=offset + (1 if seq > 1 else 0))
    for invalid in ([1], [3, 4], [7, 3], [3, 7, 12, 20, 25]):
        with pytest.raises(ValueError):
            validate_days(invalid, lane)


def test_followup_slot_is_independent_of_other_message_kinds(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An existing concept slot cannot replace or block the first follow-up sequence position."""
    first = approved_intent(db, drafting_setup)
    accept(db, delivery_setup, first, NOW)
    with db.sessions.begin() as session:
        session.add(
            Draft(
                conversation_id=first.conversation_id,
                kind="concept",
                step=1,
                sequence_step=0,
                revision=1,
                snapshot={"body": "A different concept message"},
                digest="x" * 64,
                state="sent",
                created_by=drafting_setup["actor_id"],
            )
        )
    follow = make_draft(db, drafting_setup, step=1)
    assert follow.kind == "follow_up" and follow.sequence_step == 1 and follow.step == 2
    approve(db, follow.id, follow.digest, drafting_setup["actor_id"])
    item = schedule(db, follow.id, drafting_setup["actor_id"], NOW)
    assert item.due_at >= NOW + timedelta(days=3)
    with pytest.raises(ValueError, match="own angle"):
        make_draft(db, drafting_setup, step=2, body=BODY)


def test_owner_campaign_pause_cancels_remaining_steps(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Turning off a campaign is a cancellation, not a silently resumable schedule."""
    item = approved_intent(db, drafting_setup)
    with db.sessions() as session:
        campaign = session.get(Campaign, drafting_setup["campaign_id"])
        assert campaign
    edit_campaign(
        db,
        campaign.id,
        campaign.version,
        drafting_setup["actor_id"],
        name=campaign.name,
        industry=campaign.industry,
        area=campaign.area,
        country=campaign.country,
        services=campaign.services,
        daily_limit=campaign.daily_limit,
        active=False,
    )
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        assert saved and saved.state == "cancelled"


def test_hostinger_rolling_day_survives_calendar_reset(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Yesterday's attempts still consume Hostinger's rolling allowance."""
    select_provider(db, drafting_setup, "hostinger")
    item = approved_intent(db, drafting_setup)
    with db.sessions.begin() as session:
        state = session.get(MailboxState, item.mailbox_id)
        assert state
        state.daily_limit = 1
        session.add(
            SendAttempt(
                intent_id=item.id,
                mailbox_id=item.mailbox_id,
                started_at=NOW - timedelta(hours=20),
                outcome="accepted",
            )
        )
    transport = FakeTransport()
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
        assert saved and saved.hold_reason == "rolling_24h_limit"


@pytest.mark.parametrize("provider", ["titan", "hostinger", "gmail"])
def test_provider_settings_and_starttls(provider: str, settings: Settings) -> None:
    """Endpoints are per-mailbox and STARTTLS finishes before credentials are used."""
    box = Mailbox(provider=provider, connection=connection(provider, {"smtp_port": 587}))
    config = mailbox_settings(settings, box)
    assert config.mail_smtp_host == PRESETS[provider]["smtp_host"]
    client = Mock()
    client.mail.return_value = client.rcpt.return_value = client.data.return_value = (250, b"ok")
    result = transmit(
        config,
        "sales@example.org",
        "fake",
        "info@example.com",
        b"test",
        before_data=lambda: True,
        factory=lambda: client,
    )
    assert result.outcome == "accepted"
    calls = [call[0] for call in client.method_calls]
    assert calls.index("starttls") < calls.index("login") < calls.index("data")
    for bad in (
        {"smtp_host": "127.0.0.1"},
        {"smtp_host": "smtp.gmail.com.evil.example"},
        {"imap_port": 143},
        {"smtp_port": 25},
    ):
        with pytest.raises(ValueError):
            connection(provider, bad)


@pytest.mark.parametrize(
    "action,status,label",
    [
        ("failed", "5.1.1", "bounce"),
        ("failed", "4.1.1", "delivery_notice"),
        ("delayed", "4.2.0", "delivery_delayed"),
        ("delayed", "5.2.0", "delivery_delayed"),
    ],
)
def test_dsn_action_and_status(action: str, status: str, label: str) -> None:
    """Only explicit permanent failures consume the bounce budget."""
    raw = (
        "From: mailer-daemon@example.org\nMIME-Version: 1.0\n"
        "Content-Type: multipart/report; boundary=x\n\n--x\n"
        "Content-Type: message/delivery-status\n\nReporting-MTA: dns; example.org\n\n"
        f"Final-Recipient: rfc822; info@example.com\nAction: {action}\nStatus: {status}\n\n--x--\n"
    )
    assert classify(parse_reply(raw.encode())) == label
