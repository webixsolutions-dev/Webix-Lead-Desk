"""Read-only allowance and usage projections; never reconcile or initiate provider calls."""

import calendar
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select

from app.audit.monthly import CAPS, bounds
from app.core.timezones import zone
from app.db.acquisition_models import SearchBucket, SearchRecord
from app.db.lead_models import Campaign
from app.db.outreach_models import DraftApproval
from app.db.research_models import AICall, BudgetAccount
from app.db.session import Database
from app.sources.enrichment.config import get_config
from app.sources.enrichment.quota import bucket_keys

LABELS = {
    "enrichment": "Lead finding",
    "audits": "Business audits",
    "email_drafts": "Email drafts",
    "linkedin": "LinkedIn",
    "replies": "Replies",
    "reserve": "Other AI work",
}


def dollars(amount: int | Decimal | None) -> str:
    """Round USD millionths once for presentation, retaining exact ledger arithmetic."""
    if amount is None:
        return "—"
    value = (Decimal(amount) / 1_000_000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${value:,.2f}"


def allowance_summary(db: Database, now: datetime) -> dict[str, Any]:
    """Keep confirmed charges, pending/uncertain reserves and expired allowance distinct."""
    with db.sessions() as session:
        account = session.get(BudgetAccount, "primary")
        confirmed, reserved, uncertain = session.execute(
            select(
                func.coalesce(func.sum(AICall.charged), 0),
                func.coalesce(
                    func.sum(case((AICall.charged.is_(None), AICall.reserved), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (AICall.charged.is_(None)) & (AICall.state == "uncertain"),
                                AICall.reserved,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
        ).one()
        allowance = account.allowance if account else 0
        # The funding gate is authoritative if a historical ledger discrepancy exists.
        committed = max(confirmed + reserved, account.committed if account else 0)
        expiry = account.expires_at if account else None
    percent = min(100, committed * 100 // allowance) if allowance else 0
    expired = expiry is not None and expiry <= now
    return {
        "allowance": allowance,
        "confirmed": confirmed,
        "reserved": reserved,
        "uncertain": uncertain,
        "committed": committed,
        "expires_at": expiry,
        "unallocated": max(0, allowance - committed),
        "available": max(0, allowance - committed) if expiry and not expired else 0,
        "expired": expired,
        "percent": percent,
        "tone": "red" if expired or percent > 90 else "amber" if percent >= 70 else "green",
        "reconciliation": committed - confirmed - reserved,
    }


def usage_report(db: Database, now: datetime) -> dict[str, Any]:
    """Aggregate month-to-date costs without loading provider results or inferring campaigns."""
    start, end = bounds(now)
    local = now.astimezone(zone("America/Edmonton"))
    week_start = (local - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    config, _ = get_config(db)
    with db.sessions() as session:
        rows = session.execute(
            select(
                AICall.id,
                AICall.created_at,
                AICall.category,
                AICall.model,
                AICall.company_id,
                AICall.charged,
                AICall.reserved,
            ).where(
                AICall.created_at >= min(start, week_start.astimezone(UTC)),
                AICall.created_at <= now,
            )
        ).all()
        # Only a stored, unambiguous provider-call association can name a campaign.
        attribution = {
            call_id: name
            for call_id, name in session.execute(
                select(SearchRecord.ai_call_id, func.min(Campaign.name))
                .join(Campaign, Campaign.id == SearchRecord.campaign_id)
                .where(
                    SearchRecord.ai_call_id.is_not(None),
                    SearchRecord.created_at >= start,
                    SearchRecord.created_at < end,
                )
                .group_by(SearchRecord.ai_call_id)
                .having(func.count(func.distinct(SearchRecord.campaign_id)) == 1)
            ).all()
            if call_id is not None
        }
        approvals = (
            session.scalar(
                select(func.count())
                .select_from(DraftApproval)
                .where(DraftApproval.approved_at >= start, DraftApproval.approved_at <= now)
            )
            or 0
        )
        bucket = session.get(SearchBucket, bucket_keys(config.provider, now)[0])
        searches = bucket.calls if bucket else 0
    days: dict[str, int] = {
        (local - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(6, -1, -1)
    }
    categories: dict[str, list[int]] = {key: [0, 0] for key in CAPS}
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    businesses: set[str] = set()
    confirmed = reserved = 0
    for row in rows:
        charged = row.charged or 0
        pending = row.reserved if row.charged is None else 0
        day = row.created_at.astimezone(zone("America/Edmonton")).strftime("%Y-%m-%d")
        if day in days:
            days[day] += charged
        if not start <= row.created_at < end:
            continue
        confirmed += charged
        reserved += pending
        if row.company_id:
            businesses.add(row.company_id)
        category = categories.setdefault(row.category, [0, 0])
        category[0] += charged
        category[1] += pending
        group = groups[(day, attribution.get(row.id, "Unattributed"), row.model)]
        group[0] += charged
        group[1] += pending
        group[2] += 1
    return {
        "month": local.strftime("%B %Y"),
        "confirmed": confirmed,
        "reserved": reserved,
        "today": days[local.strftime("%Y-%m-%d")],
        "days": [{"day": day, "amount": amount} for day, amount in days.items()],
        "chart_max": max(1, *days.values()),
        "categories": [
            {
                "name": LABELS.get(key, key),
                "confirmed": value[0],
                "reserved": value[1],
                "cap": CAPS.get(key, 0),
            }
            for key, value in categories.items()
        ],
        "detail": [
            {
                "day": key[0],
                "campaign": key[1],
                "model": key[2],
                "confirmed": value[0],
                "reserved": value[1],
                "calls": value[2],
            }
            for key, value in sorted(groups.items(), reverse=True)
        ],
        "per_lead": Decimal(confirmed) / len(businesses) if businesses else None,
        "per_draft": Decimal(confirmed) / approvals if approvals else None,
        "lead_count": len(businesses),
        "approval_count": approvals,
        "projection": Decimal(confirmed)
        * calendar.monthrange(local.year, local.month)[1]
        / local.day,
        "searches": searches,
        "search_cap": config.places_daily_calls
        if config.provider == "google_places"
        else config.daily_calls,
    }
