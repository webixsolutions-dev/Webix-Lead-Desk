"""Read-only desktop aggregates; never read message bodies or call paid providers."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, select, text

from app.core.config import Settings
from app.core.preferences import Preferences
from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Contact
from app.db.models import WorkspaceSetting
from app.db.outreach_models import Conversation, Draft
from app.db.session import Database

Period = Literal["Today", "Last 7 days", "Last 30 days", "All time"]
PERIODS: tuple[Period, ...] = ("Today", "Last 7 days", "Last 30 days", "All time")
HUMAN_REPLIES = ("interested", "needs_review", "not_interested", "unsubscribe")


@dataclass(frozen=True)
class DailyActivity:
    """A local calendar day's accepted emails and distinct replying businesses."""

    label: str
    sent: int
    replied: int


@dataclass(frozen=True)
class DesktopSnapshot:
    """Aggregate values only; no credentials, names, addresses or message text."""

    checked_at: datetime
    timezone: str
    period: Period
    contacted: int
    sent: int
    replied: int
    approvals: int
    scheduled: int
    attention: int
    meetings: int
    days: tuple[DailyActivity, ...]


def read_snapshot(
    db: Database, settings: Settings, period: Period, now: datetime
) -> DesktopSnapshot:
    """Query consistent aggregates with local calendar boundaries and bounded SQL waits."""
    if period not in PERIODS or now.tzinfo is None:
        raise ValueError("A supported period and aware timestamp are required")
    with db.sessions() as session:
        if db.engine.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '2000ms'"))
        stored = session.get(WorkspaceSetting, "workspace")
        timezone = (
            Preferences.model_validate(stored.value).display_timezone
            if stored
            else settings.display_timezone
        )
        zone = ZoneInfo(timezone)
        today = now.astimezone(zone).date()
        midnight = datetime.combine(today, time.min, zone)
        start = (
            datetime(1970, 1, 1, tzinfo=UTC)
            if period == "All time"
            else midnight
            - timedelta(days={"Today": 0, "Last 7 days": 6, "Last 30 days": 29}[period])
        )
        accepted = (
            select(Conversation.company_id, SendIntent.accepted_at.label("at"))
            .join(SendIntent, SendIntent.conversation_id == Conversation.id)
            .where(SendIntent.state == "accepted", SendIntent.accepted_at <= now)
            .subquery()
        )
        prior_send = (
            select(SendIntent.id)
            .where(
                SendIntent.conversation_id == Conversation.id,
                SendIntent.mailbox_id == InboxMessage.mailbox_id,
                SendIntent.state == "accepted",
                SendIntent.accepted_at <= InboxMessage.received_at,
            )
            .exists()
        )
        replies = (
            select(Conversation.company_id, InboxMessage.received_at.label("at"))
            .join(InboxMessage, InboxMessage.conversation_id == Conversation.id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(
                InboxMessage.classification.in_(HUMAN_REPLIES),
                InboxMessage.mailbox_id == Conversation.mailbox_id,
                func.lower(InboxMessage.sender) == func.lower(Contact.email),
                InboxMessage.received_at <= now,
                prior_send,
            )
            .subquery()
        )
        totals = session.execute(
            select(func.count(), func.count(func.distinct(accepted.c.company_id))).where(
                accepted.c.at >= start
            )
        ).one()
        replied = session.scalar(
            select(func.count(func.distinct(replies.c.company_id))).where(replies.c.at >= start)
        )
        boundaries = [midnight - timedelta(days=6 - i) for i in range(8)]
        sent_days = session.execute(
            select(
                *[
                    func.count(case((and_(accepted.c.at >= a, accepted.c.at < b), 1)))
                    for a, b in zip(boundaries, boundaries[1:], strict=False)
                ]
            ).where(accepted.c.at >= boundaries[0])
        ).one()
        reply_days = session.execute(
            select(
                *[
                    func.count(
                        func.distinct(
                            case((and_(replies.c.at >= a, replies.c.at < b), replies.c.company_id))
                        )
                    )
                    for a, b in zip(boundaries, boundaries[1:], strict=False)
                ]
            ).where(replies.c.at >= boundaries[0])
        ).one()
        approvals = session.scalar(
            select(func.count()).select_from(Draft).where(Draft.state == "draft")
        )
        scheduled = session.scalar(
            select(func.count()).select_from(SendIntent).where(SendIntent.state == "scheduled")
        )
        attention = session.scalar(
            select(func.count())
            .select_from(SendIntent)
            .where(SendIntent.state.in_(("held", "uncertain")))
        )
        meetings = session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.state == "confirmed",
                Booking.starts_at >= now,
                Booking.starts_at < now + timedelta(days=7),
            )
        )
        return DesktopSnapshot(
            now,
            timezone,
            period,
            int(totals[1]),
            int(totals[0]),
            int(replied or 0),
            int(approvals or 0),
            int(scheduled or 0),
            int(attention or 0),
            int(meetings or 0),
            tuple(
                DailyActivity(a.strftime("%a %d"), int(sent_days[i]), int(reply_days[i]))
                for i, a in enumerate(boundaries[:7])
            ),
        )
