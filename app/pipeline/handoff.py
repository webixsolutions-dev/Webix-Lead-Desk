"""Calgary call handoff times with an explicit one-business-hour response target."""

from datetime import datetime, time, timedelta

from app.sending.window import CALGARY


def call_due(now: datetime) -> datetime:
    """Add one working hour across Monday–Friday 09:00–17:00 Calgary, respecting DST."""
    local = now.astimezone(CALGARY)
    remaining = timedelta(hours=1)
    while True:
        day = local.date()
        if local.weekday() >= 5 or local.time() >= time(17):
            day += timedelta(days=1)
            while day.weekday() >= 5:
                day += timedelta(days=1)
            local = datetime.combine(day, time(9), CALGARY)
        elif local.time() < time(9):
            local = datetime.combine(day, time(9), CALGARY)
        end = datetime.combine(local.date(), time(17), CALGARY)
        available = end - local
        if remaining <= available:
            return local + remaining
        remaining -= available
        local = end
