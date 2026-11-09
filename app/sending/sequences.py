"""Lane-specific sequence timing measured from accepted first contact."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.delivery_models import SendIntent
from app.db.lead_models import Campaign
from app.db.outreach_models import Draft


def days_for(campaign: Campaign) -> list[int]:
    """Return reviewed offsets or conservative lane defaults."""
    return (
        campaign.followup_days
        if campaign.followup_days is not None
        else ([4, 10] if campaign.lane == "partner" else [3, 7, 12, 20])
    )


def validate_days(days: list[int], lane: str) -> list[int]:
    """Bound sequence length and enforce at least two days between every step."""
    if (
        len(days) > (2 if lane == "partner" else 4)
        or any(type(day) is not int or not 2 <= day <= 90 for day in days)
        or any(right - left < 2 for left, right in zip([0] + days, days, strict=False))
    ):
        raise ValueError("Use up to four client or two partner steps, at least two days apart")
    return days


def earliest_followup(session: Session, draft: Draft) -> datetime:
    """Require accepted predecessors, applying campaign offsets and minimum actual spacing."""
    campaign = session.get(Campaign, draft.snapshot["campaign_id"])
    assert campaign
    days = days_for(campaign)
    step = draft.sequence_step
    if draft.kind != "follow_up" or not 1 <= step <= len(days):
        raise ValueError("This follow-up is not enabled in the campaign sequence")
    first = session.scalar(
        select(SendIntent)
        .join(Draft)
        .where(
            SendIntent.conversation_id == draft.conversation_id,
            Draft.kind == "first_touch",
            SendIntent.state == "accepted",
        )
    )
    previous = (
        first
        if step == 1
        else session.scalar(
            select(SendIntent)
            .join(Draft)
            .where(
                SendIntent.conversation_id == draft.conversation_id,
                Draft.kind == "follow_up",
                Draft.sequence_step == step - 1,
                SendIntent.state == "accepted",
            )
        )
    )
    if not first or not first.accepted_at or not previous or not previous.accepted_at:
        raise ValueError("Follow-up requires confirmed delivery of the previous step")
    return max(
        first.accepted_at + timedelta(days=days[step - 1]), previous.accepted_at + timedelta(days=2)
    )
