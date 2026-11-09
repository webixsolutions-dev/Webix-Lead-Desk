"""Hard non-carrying monthly AI limits, including reservations with unknown outcomes."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timezones import zone
from app.db.research_models import AICall

CAPS = {
    "enrichment": 40_000_000,
    "audits": 25_000_000,
    "email_drafts": 25_000_000,
    "linkedin": 25_000_000,
    "replies": 20_000_000,
    "reserve": 35_000_000,
}
TOTAL = 170_000_000


def bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return Calgary calendar-month UTC boundaries using bundled timezone rules."""
    local = now.astimezone(zone("America/Edmonton"))
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def usage(session: Session, now: datetime) -> dict[str, int]:
    """Count known actual cost and the full reserve for every uncertain provider call."""
    start, end = bounds(now)
    values = dict.fromkeys(CAPS, 0)
    for category, amount in session.execute(
        select(AICall.category, func.sum(func.coalesce(AICall.charged, AICall.reserved)))
        .where(AICall.created_at >= start, AICall.created_at < end)
        .group_by(AICall.category)
    ):
        values[category] = int(amount)
    return values


def check(session: Session, category: str, maximum: int, now: datetime) -> None:
    """Check while the caller holds the singleton funding-row write lock."""
    from app.audit.budget import BudgetBlocked

    used = usage(session, now)
    if (
        category not in CAPS
        or used.get(category, 0) + maximum > CAPS[category]
        or sum(used.values()) + maximum > TOTAL
    ):
        raise BudgetBlocked("Monthly AI category or $170 workspace ceiling reached")
