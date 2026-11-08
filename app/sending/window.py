"""Calgary weekday windows and conservative mailbox ramp limits with DST support."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db.delivery_models import MailboxState

CALGARY = ZoneInfo("America/Edmonton")


def within_window(now: datetime) -> bool:
    """Allow only Monday–Friday 08:30 inclusive to 11:30 exclusive in Calgary."""
    local = now.astimezone(CALGARY)
    return local.weekday() < 5 and time(8, 30) <= local.time() < time(11, 30)


def next_window(now: datetime) -> datetime:
    """Return the earliest allowable instant without carrying missed sends into a burst."""
    if within_window(now):
        return now
    local = now.astimezone(CALGARY)
    day = local.date()
    if local.time() >= time(11, 30):
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(8, 30), CALGARY)


def ramp_limit(state: MailboxState, now: datetime) -> int:
    """Cap owner limits by 10/20/35/50 across successive weeks after activation."""
    if state.ramp_started_at is None:
        return 0
    elapsed = max(
        0, (now.astimezone(CALGARY).date() - state.ramp_started_at.astimezone(CALGARY).date()).days
    )
    cap = (10, 20, 35, 50)[min(elapsed // 7, 3)]
    return min(state.daily_limit, cap)


def end_of_day(now: datetime) -> datetime:
    """Return the next Calgary midnight, respecting daylight-saving changes."""
    return datetime.combine(now.astimezone(CALGARY).date() + timedelta(days=1), time(), CALGARY)
