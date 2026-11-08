"""Approval, thread locks, failure recovery, ramp limits and Calgary delivery windows."""

from datetime import timedelta
from email import policy
from email.parser import BytesParser

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.delivery_models import InboxCursor, MailboxDay, MailboxState, SendAttempt, SendIntent
from app.db.models import Job
from app.db.outreach_models import Conversation
from app.db.session import Database
from app.drafting.service import approve
from app.inbox.service import record_reply
from app.jobs.queue import acquire_worker, claim_next, finish_job
from app.policy.contacts import reserve_channel, suppress
from app.sending.policy import delivery_blocks
from app.sending.reservations import reserve
from app.sending.schedule import plan_jobs, schedule
from app.sending.service import dispatch, reconcile
from app.sending.window import CALGARY, next_window, ramp_limit, within_window
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send, reply_bytes
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


def test_nothing_scheduled_without_exact_approval(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Neither a new draft nor a previously approved superseded revision can schedule."""
    draft = make_draft(db, drafting_setup)
    with pytest.raises(ValueError, match="Approve the exact"):
        schedule(db, draft.id, drafting_setup["actor_id"], NOW)
    approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
    item = schedule(db, draft.id, drafting_setup["actor_id"], NOW)
    new = make_draft(
        db, drafting_setup, previous_id=draft.id, body=draft.snapshot["body"] + " Thanks."
    )
    with pytest.raises(ValueError):
        schedule(db, new.id, drafting_setup["actor_id"], NOW)
    approve(db, new.id, new.digest, drafting_setup["actor_id"])
    assert schedule(db, new.id, drafting_setup["actor_id"], NOW).id == item.id


def test_accepted_first_and_followup_keep_sender_thread_and_subject(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """A separately approved follow-up uses the original sender and Message-ID references."""
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    transport = FakeTransport()
    assert "accepted" in dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert finish_job(db, job, NOW, result="done")
    with db.sessions() as session:
        conversation = session.get(Conversation, item.conversation_id)
        assert (
            conversation
            and conversation.locked_at
            and conversation.first_message_id == item.message_id
        )
    later = NOW + timedelta(days=3)
    with pytest.raises(ValueError, match="original subject"):
        make_draft(db, drafting_setup, step=1, subject="A different topic")
    followup = approved_intent(db, drafting_setup, later, step=1)
    job2 = claim_send(db, delivery_setup, followup, later)
    transport2 = FakeTransport(later)
    assert "accepted" in dispatch(
        db, delivery_setup, job2, transport=transport2, clock=lambda: later
    )
    first = BytesParser(policy=policy.default).parsebytes(transport.wires[0])
    second = BytesParser(policy=policy.default).parsebytes(transport2.wires[0])
    assert second["From"] == first["From"] and second["Subject"] == first["Subject"]
    assert second["In-Reply-To"] == first["Message-ID"]
    assert second["References"] == first["Message-ID"]
    assert second["Message-ID"] != first["Message-ID"]
    assert second["List-Unsubscribe"] and not second.is_multipart()


def test_uncertain_smtp_never_resends_and_only_matching_sent_copy_reconciles(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Lost acknowledgement freezes identity and preserves one original wire message."""
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    transport = FakeTransport(outcome="raise")
    assert "uncertain" in dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert len(transport.wires) == 1
    assert not reconcile(db, delivery_setup, item.id, finder=lambda *args: [])
    assert not reconcile(
        db, delivery_setup, item.id, finder=lambda *args: [reply_bytes("unrelated")]
    )
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        assert saved and saved.state == "uncertain" and saved.attempts == 1
        conversation = session.get(Conversation, item.conversation_id)
        assert conversation and conversation.locked_at and conversation.state == "held"
    assert reconcile(db, delivery_setup, item.id, finder=lambda *args: transport.wires)
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        conversation = session.get(Conversation, item.conversation_id)
        assert saved and saved.state == "accepted" and conversation and conversation.state == "held"


@pytest.mark.parametrize(
    "content,automatic", [("Sounds good", False), ("Anything", True), ("", False)]
)
def test_any_reply_cancels_followup_before_data(
    db: Database,
    delivery_setup: Settings,
    drafting_setup: dict[str, str],
    content: str,
    automatic: bool,
) -> None:
    """Human, automatic and ambiguous replies all prevent a queued follow-up from entering DATA."""
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    dispatch(db, delivery_setup, job, transport=FakeTransport(), clock=lambda: NOW)
    finish_job(db, job, NOW, result="done")
    later = NOW + timedelta(days=3)
    followup = approved_intent(db, drafting_setup, later, 1)
    job2 = claim_send(db, delivery_setup, followup, later)
    transport = FakeTransport(later)
    transport.on_data = lambda: record_reply(
        db,
        item.mailbox_id,
        reply_bytes(content, item.message_id, automatic=automatic),
        "1:9",
        later,
    )
    dispatch(db, delivery_setup, job2, transport=transport, clock=lambda: later)
    assert transport.wires == []
    with db.sessions() as session:
        saved = session.get(SendIntent, followup.id)
        assert saved and saved.state == "cancelled"


def test_unapproved_edit_blocks_previously_scheduled_send(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """A schedule cannot preserve approval after an owner changes the message."""
    item = approved_intent(db, drafting_setup)
    make_draft(db, drafting_setup, previous_id=item.draft_id, subject="Updated question")
    job = claim_send(db, delivery_setup, item)
    transport = FakeTransport()
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert not transport.wires
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        assert saved and saved.state == "held"


@pytest.mark.parametrize(
    "restriction", ["ramp", "spacing", "hourly", "form", "suppression", "pause", "window"]
)
def test_dispatch_limits_and_channel_conflicts(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], restriction: str
) -> None:
    """Every restrictive condition prevents DATA without pretending the message was sent."""
    item = approved_intent(db, drafting_setup)
    with db.sessions.begin() as session:
        state = session.get(MailboxState, item.mailbox_id)
        assert state
        if restriction == "ramp":
            session.add(MailboxDay(mailbox_id=item.mailbox_id, local_day="2026-11-09", reserved=10))
        if restriction == "spacing":
            state.next_send_at = NOW + timedelta(minutes=8)
        if restriction == "hourly":
            state.hourly_limit = 1
            session.add(SendAttempt(intent_id=item.id, mailbox_id=item.mailbox_id, started_at=NOW))
        if restriction == "pause":
            state.paused_until = NOW + timedelta(hours=10)
    if restriction == "form":
        assert reserve_channel(db, drafting_setup["company_id"], "form")
    if restriction == "suppression":
        suppress(db, "service@example.com", "Recipient stop")
    now = NOW + timedelta(hours=4) if restriction == "window" else NOW
    job = claim_send(db, delivery_setup, item, now)
    transport = FakeTransport(now)
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: now)
    assert not transport.wires


def test_expired_worker_does_not_replay_transmitting_intent(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Crash recovery distinguishes irreversible sends from retryable read-only jobs."""
    item = approved_intent(db, drafting_setup)
    FakeTransport().sync(db, delivery_setup, item.mailbox_id)
    job = claim_send(db, delivery_setup, item)
    assert reserve(db, delivery_setup, job, item.id, NOW)
    later = NOW + timedelta(minutes=5)
    assert acquire_worker(db, delivery_setup, "replacement-worker", later)
    assert claim_next(db, delivery_setup, "replacement-worker", later) is None
    with db.sessions() as session:
        saved = session.get(SendIntent, item.id)
        expired = session.get(Job, job.id)
        assert saved and saved.state == "uncertain"
        assert expired and expired.state == "failed" and expired.claim_owner is None
    plan_jobs(db, delivery_setup, later)
    with db.sessions() as session:
        assert len(list(session.scalars(select(Job).where(Job.kind == "send")))) == 1


def test_temporary_rejection_retries_same_identity_after_spacing(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An explicit pre-acceptance negative reply can retry using the same stable wire message."""
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    transport = FakeTransport(outcome="temporary")
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    finish_job(db, job, NOW, result="temporary")
    later = NOW + timedelta(minutes=10)
    job2 = claim_send(db, delivery_setup, item, later)
    transport2 = FakeTransport(later)
    dispatch(db, delivery_setup, job2, transport=transport2, clock=lambda: later)
    assert transport.wires == transport2.wires and len(transport.wires) == 1


def test_stale_inbox_and_disabled_worker_block_send(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An old sync cannot authorize delivery and a disabled process never contacts the mailbox."""
    item = approved_intent(db, drafting_setup)
    transport = FakeTransport()
    transport.sync(db, delivery_setup, item.mailbox_id)
    with db.sessions.begin() as session:
        cursor = session.get(InboxCursor, item.mailbox_id)
        assert cursor
        cursor.synced_at = NOW - timedelta(minutes=16)
    assert "inbox_not_fresh" in delivery_blocks(db, delivery_setup, item, NOW)
    job = claim_send(db, delivery_setup, item)
    disabled = delivery_setup.model_copy(update={"live_send_enabled": False})
    assert "disabled" in dispatch(db, disabled, job, transport=transport, clock=lambda: NOW)
    assert not transport.wires


def test_dst_weekend_and_ramp_boundaries() -> None:
    """Calgary clock windows survive DST and the owner can only lower the ramp ceiling."""
    state = MailboxState(daily_limit=50, ramp_started_at=NOW)
    assert [ramp_limit(state, NOW + timedelta(days=d)) for d in (0, 6, 7, 14, 21)] == [
        10,
        10,
        20,
        35,
        50,
    ]
    state.daily_limit = 8
    assert ramp_limit(state, NOW + timedelta(days=21)) == 8
    state.ramp_started_at = None
    assert ramp_limit(state, NOW) == 0
    assert within_window(NOW) and not within_window(NOW - timedelta(days=1))
    assert next_window(NOW - timedelta(days=1)).astimezone(CALGARY).hour == 8
    assert next_window(NOW + timedelta(hours=3)).day == 10
    summer = NOW.replace(month=7, day=6, hour=15)
    assert within_window(summer) and summer.astimezone(CALGARY).hour == 9


def test_rescheduling_unattempted_revision_updates_sender_reservation(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An unlocked sender change replaces the intent's routing before any SMTP attempt."""
    from app.db.outreach_models import Mailbox

    item = approved_intent(db, drafting_setup)
    with db.sessions.begin() as session:
        box = Mailbox(
            email="second@example.net",
            lane="client",
            brand="Synthetic studio",
            sender_name="Second Owner",
            mailing_address="1 Synthetic Street Calgary",
            contact_details="second@example.net",
        )
        session.add(box)
        session.flush()
        mailbox_id = box.id
    new = make_draft(db, drafting_setup, previous_id=item.draft_id, mailbox_id=mailbox_id)
    approve(db, new.id, new.digest, drafting_setup["actor_id"])
    changed = schedule(db, new.id, drafting_setup["actor_id"], NOW)
    assert changed.id == item.id and changed.mailbox_id == mailbox_id
    assert changed.message_id.endswith("@example.net>")
