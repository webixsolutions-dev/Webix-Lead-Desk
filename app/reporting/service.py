"""Aggregate persisted outcomes by campaign and sector without multiplying joined events."""

from typing import Any

from sqlalchemy import func, select

from app.db.delivery_models import InboxMessage, SendIntent
from app.db.form_models import FormDraft
from app.db.lead_models import Campaign, CampaignLead
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity
from app.db.session import Database


def campaign_results(db: Database) -> list[dict[str, Any]]:
    """Report known acceptances and recorded sales values; neither implies cash collected."""
    with db.sessions() as session:
        campaigns = list(session.scalars(select(Campaign).order_by(Campaign.created_at.desc())))
        leads: dict[str, int] = {
            key: count
            for key, count in session.execute(
                select(CampaignLead.campaign_id, func.count()).group_by(CampaignLead.campaign_id)
            ).all()
        }
        results: dict[str, dict[str, Any]] = {
            item.id: {
                "campaign": item,
                "leads": leads.get(item.id, 0),
                "first_sent": 0,
                "followups": 0,
                "concepts": 0,
                "replied": 0,
                "qualified": 0,
                "won": 0,
                "setup_cad": 0,
                "monthly_cad": 0,
                "forms": 0,
            }
            for item in campaigns
        }
        for campaign_id, step, count in session.execute(
            select(Conversation.campaign_id, SendIntent.step, func.count())
            .join(SendIntent, SendIntent.conversation_id == Conversation.id)
            .where(SendIntent.state == "accepted")
            .group_by(Conversation.campaign_id, SendIntent.step)
        ):
            key = "first_sent" if step == 0 else "followups" if step == 1 else "concepts"
            results[campaign_id][key] += count
        for campaign_id, count in session.execute(
            select(
                Conversation.campaign_id, func.count(func.distinct(InboxMessage.conversation_id))
            )
            .join(InboxMessage, InboxMessage.conversation_id == Conversation.id)
            .where(InboxMessage.classification.not_in(["bounce", "automatic"]))
            .group_by(Conversation.campaign_id)
        ):
            results[campaign_id]["replied"] = count
        for card, campaign_id in session.execute(
            select(Opportunity, Conversation.campaign_id).join(
                Conversation, Conversation.id == Opportunity.conversation_id
            )
        ):
            row = results[campaign_id]
            if card.stage not in {"Replied", "Lost"}:
                row["qualified"] += 1
            if card.stage == "Won":
                row["won"] += 1
                row["setup_cad"] += card.setup_cad_cents
                row["monthly_cad"] += card.recurring_cad_cents
        for campaign_id, count in session.execute(
            select(FormDraft.campaign_id, func.count())
            .where(FormDraft.state == "submitted")
            .group_by(FormDraft.campaign_id)
        ):
            results[campaign_id]["forms"] = count
        return list(results.values())
