"""Queue one bounded daily discovery run for the owner-selected campaign."""

from datetime import datetime

from sqlalchemy import select

from app.audit.provider_pause import paused_event
from app.core.config import Settings
from app.db.delivery_models import IntegrationSecret
from app.db.lead_models import Campaign
from app.db.models import Job, User
from app.db.research_models import BudgetAccount
from app.db.session import Database
from app.jobs.queue import enqueue
from app.operations.control import maintenance_enabled
from app.sending.window import CALGARY
from app.sources.enrichment.config import get_config


def plan_daily(db: Database, settings: Settings, now: datetime) -> None:
    """Start up to ten search batches, fenced by the local day and search budgets."""
    config, _ = get_config(db)
    if (
        not (settings.openai_enabled or settings.openai_search_enabled)
        or not config.enabled
        or config.provider != "openai_web_search"
        or min(
            config.daily_lead_target,
            config.daily_calls,
            config.monthly_calls,
            config.monthly_usd_micro,
            config.search_usd_micro,
        )
        <= 0
    ):
        return
    day = now.astimezone(CALGARY).date().isoformat()
    key = f"daily-discovery:{day}"
    with db.sessions() as session:
        campaign = session.get(Campaign, config.daily_campaign_id)
        budget = session.get(BudgetAccount, "primary")
        if (
            not campaign
            or not campaign.active
            or not budget
            or budget.expires_at <= now
            or budget.committed >= budget.allowance * 0.9
            or maintenance_enabled(session)
            or paused_event(session, "openai")
            or not (
                settings.openai_api_key
                and settings.openai_api_key.get_secret_value()
                or session.get(IntegrationSecret, "openai")
            )
            or session.scalar(select(Job.id).where(Job.unique_key == key))
        ):
            return
        actor = session.scalar(
            select(User.id)
            .where(User.active.is_(True), User.role == "owner")
            .order_by(User.id)
            .limit(1)
        )
    if actor:
        enqueue(
            db,
            key,
            "discover",
            {
                "campaign_id": config.daily_campaign_id,
                "actor_id": actor,
                "daily_target": config.daily_lead_target,
                "discovery_day": day,
                "remaining_batches": 10,
            },
            now,
        )
