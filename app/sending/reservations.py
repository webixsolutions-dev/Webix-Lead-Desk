"""Atomic capacity reservations and final pre-DATA fencing."""

from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from random import SystemRandom

from sqlalchemy import func, or_, select, update

from app.core.config import Settings
from app.db.delivery_models import MailboxDay, MailboxState, SendAttempt, SendIntent
from app.db.lead_models import Campaign
from app.db.models import Job
from app.db.outreach_models import Conversation, Draft
from app.db.session import Database
from app.pilot.service import reserve_pilot
from app.policy.contacts import reserve_channel
from app.sending.policy import claim_valid, delivery_blocks
from app.sending.window import CALGARY, end_of_day, next_window, ramp_limit


def wire_message(draft: Draft, item: SendIntent, now: datetime) -> str:
    """Construct one immutable plain-text RFC message from the exact approved snapshot."""
    snapshot = draft.snapshot
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = formataddr((snapshot["from_name"], snapshot["from_email"]))
    message["To"] = snapshot["recipient"]
    message["Subject"] = snapshot["subject"]
    message["Message-ID"] = item.message_id
    message["Date"] = format_datetime(now)
    message["List-Unsubscribe"] = f"<mailto:{snapshot['from_email']}?subject=unsubscribe>"
    if item.step:
        message["In-Reply-To"] = snapshot["in_reply_to"]
        message["References"] = " ".join(snapshot["references"])
    message.set_content(snapshot["body"] + "\n\n" + snapshot["footer"])
    return message.as_string()


def postpone(db: Database, item_id: str, due: datetime, reason: str) -> None:
    """Defer only a still-scheduled intent, leaving reply cancellation intact."""
    with db.sessions.begin() as session:
        session.execute(
            update(SendIntent)
            .where(SendIntent.id == item_id, SendIntent.state == "scheduled")
            .values(due_at=next_window(due), hold_reason=reason)
        )


def reserve(
    db: Database, settings: Settings, job: Job, item_id: str, now: datetime
) -> SendAttempt | None:
    """Lock sender, approval revision and capacity before crossing the SMTP boundary."""
    with db.sessions() as session:
        item = session.get(SendIntent, item_id)
        if not item or item.state != "scheduled" or item.due_at > now or item.attempts >= 3:
            return None
        conversation = session.get(Conversation, item.conversation_id)
        assert conversation
        company_id = conversation.company_id
    reasons = delivery_blocks(db, settings, item, now)
    if reasons:
        with db.sessions.begin() as session:
            session.execute(
                update(SendIntent)
                .where(SendIntent.id == item_id, SendIntent.state == "scheduled")
                .values(state="held", hold_reason=reasons[0][:100])
            )
        # The time window is a deferral, not an owner review failure.
        if reasons == ["outside_calgary_window"]:
            with db.sessions.begin() as session:
                session.execute(
                    update(SendIntent)
                    .where(SendIntent.id == item_id, SendIntent.state == "held")
                    .values(state="scheduled", due_at=next_window(now))
                )
        return None
    if not reserve_channel(db, company_id, "email"):
        with db.sessions.begin() as session:
            session.execute(
                update(SendIntent)
                .where(SendIntent.id == item_id, SendIntent.state == "scheduled")
                .values(state="held", hold_reason="manual_form_channel_reserved")
            )
        return None
    with db.sessions.begin() as session:
        if not claim_valid(session, job, now):
            return None
        # This conditional write serializes capacity decisions even on SQLite.
        gate = session.scalar(
            update(MailboxState)
            .where(
                MailboxState.mailbox_id == item.mailbox_id,
                or_(MailboxState.next_send_at.is_(None), MailboxState.next_send_at <= now),
                or_(MailboxState.paused_until.is_(None), MailboxState.paused_until <= now),
            )
            .values(next_send_at=now + timedelta(seconds=SystemRandom().randint(180, 480)))
            .returning(MailboxState.mailbox_id)
        )
        state = session.get(MailboxState, item.mailbox_id)
        current = session.get(SendIntent, item_id)
        if not state or not current or current.state != "scheduled":
            return None
        if gate is None:
            current.due_at = next_window(
                max(
                    now + timedelta(minutes=3), state.next_send_at or now, state.paused_until or now
                )
            )
            return None
        conversation = session.get(Conversation, item.conversation_id)
        assert conversation
        campaign = session.get(Campaign, conversation.campaign_id)
        assert campaign
        local_day = now.astimezone(CALGARY).date().isoformat()
        day = session.scalar(
            select(MailboxDay).where(
                MailboxDay.mailbox_id == item.mailbox_id, MailboxDay.local_day == local_day
            )
        )
        if day is None:
            day = MailboxDay(mailbox_id=item.mailbox_id, local_day=local_day, reserved=0, bounces=0)
            session.add(day)
        hour_count = (
            session.scalar(
                select(func.count())
                .select_from(SendAttempt)
                .where(
                    SendAttempt.mailbox_id == item.mailbox_id,
                    SendAttempt.started_at > now - timedelta(hours=1),
                )
            )
            or 0
        )
        # Campaign caps count all attempts across its conversations, including follow-ups.
        campaign_count = (
            session.scalar(
                select(func.count())
                .select_from(SendAttempt)
                .join(SendIntent)
                .join(Conversation)
                .where(
                    Conversation.campaign_id == campaign.id,
                    SendAttempt.started_at >= end_of_day(now - timedelta(days=1)),
                )
            )
            or 0
        )
        if day.reserved >= ramp_limit(state, now) or campaign_count >= campaign.daily_limit:
            current.due_at, current.hold_reason = next_window(end_of_day(now)), "daily_limit"
            return None
        if hour_count >= state.hourly_limit:
            current.due_at, current.hold_reason = (
                next_window(now + timedelta(hours=1)),
                "hourly_limit",
            )
            return None
        changed = session.scalar(
            update(Draft)
            .where(
                Draft.id == current.draft_id,
                Draft.state == "approved",
                Draft.digest == current.approval_digest,
            )
            .values(state="sending")
            .returning(Draft.id)
        )
        if not changed or conversation.state in {"replied", "held", "stopped"}:
            current.state, current.hold_reason = "held", "approval_or_conversation_changed"
            return None
        draft = session.get(Draft, current.draft_id)
        assert draft
        if not reserve_pilot(session, current, company_id):
            current.state, current.hold_reason = "held", "pilot_capacity_or_state_changed"
            draft.state = "approved"
            return None
        day.reserved += 1
        conversation.locked_at = conversation.locked_at or now
        current.state, current.attempted_at = "transmitting", now
        current.attempts += 1
        current.wire_message = current.wire_message or wire_message(draft, current, now)
        attempt = SendAttempt(intent_id=current.id, mailbox_id=current.mailbox_id, started_at=now)
        session.add(attempt)
        session.flush()
        return attempt


def before_data(db: Database, settings: Settings, job: Job, item_id: str, now: datetime) -> bool:
    """Recheck policy and fencing at the last possible point before SMTP DATA."""
    with db.sessions() as session:
        item = session.get(SendIntent, item_id)
        if not item or item.state != "transmitting" or not claim_valid(session, job, now):
            return False
    return not delivery_blocks(db, settings, item, now, transmitting=True)
