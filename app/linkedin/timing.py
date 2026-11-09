"""DST-aware weekday task windows, independent from the narrower email window."""

from datetime import UTC, date, datetime, time, timedelta

from app.sending.window import CALGARY


def local_day(now: datetime) -> str:
    """Return the Calgary date used by every persistent quota and daily fence."""
    return now.astimezone(CALGARY).date().isoformat()


def daytime(now: datetime, *, planning: bool = False) -> bool:
    """Plan after 07:30 and allow human work 09:00–17:00 on Calgary weekdays."""
    local = now.astimezone(CALGARY)
    start = time(7, 30) if planning else time(9)
    return local.weekday() < 5 and start <= local.time() < time(17)


def slots(now: datetime, count: int) -> list[datetime]:
    """Spread missed work across remaining batches or five-minute afternoon slots."""
    if count < 1 or not daytime(now, planning=True):
        return []
    local = now.astimezone(CALGARY)
    future = [
        datetime.combine(local.date(), t, CALGARY) for t in (time(9), time(11, 30), time(14, 30))
    ]
    future = [at for at in future if at >= local + timedelta(seconds=1)]
    if not future:
        future = [local + timedelta(minutes=5)]
    end = datetime.combine(local.date(), time(17), CALGARY)
    result = []
    for index in range(count):
        at = future[index % len(future)] + timedelta(minutes=5 * (index // len(future)))
        if at < end:
            result.append(at.astimezone(UTC))
    return sorted(set(result))


def week_start(now: datetime) -> str:
    """Use a Monday-based local week, not a rolling UTC week."""
    day = date.fromisoformat(local_day(now))
    return (day - timedelta(days=day.weekday())).isoformat()
