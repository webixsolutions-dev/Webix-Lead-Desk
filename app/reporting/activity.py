"""Low-cost projections of persisted worker progress, never inferred provider activity."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.db.acquisition_models import LeadEvent
from app.db.delivery_models import SendIntent
from app.db.lead_models import Campaign, CampaignLead, Company
from app.db.models import Job
from app.db.outreach_models import Conversation, Draft, DraftApproval
from app.db.research_models import Evidence, WebsiteAudit
from app.db.session import Database

LABELS = {
    "found": "Business found",
    "enriched": "Business details recorded",
    "audited": "Website audit recorded",
    "drafted": "Draft prepared",
    "draft_edited": "Draft updated",
    "approved": "Exact message approved",
    "sent": "Email accepted by sender",
    "replied": "Reply received",
    "followup_sent": "Follow-up accepted by sender",
    "call": "Call outcome recorded",
}
JOB_LABELS = {
    "calgary": "Open Calgary licence data",
    "discover": "Find businesses",
    "health": "Worker health check",
}


def activity(db: Database, now: datetime) -> dict[str, Any]:
    """Read recent events and cumulative per-campaign distinct-business counts."""
    with db.sessions() as session:
        feed = [
            {
                "at": event.occurred_at,
                "text": LABELS.get(event.kind, event.kind.replace("_", " ")),
                "detail": name,
                "url": "/leads/" + event.company_id,
            }
            for event, name in session.execute(
                select(LeadEvent, Company.name)
                .join(Company)
                .order_by(LeadEvent.occurred_at.desc())
                .limit(20)
            )
        ]
        for job in session.scalars(select(Job).order_by(Job.created_at.desc()).limit(10)):
            feed.append(
                {
                    "at": job.finished_at or job.created_at,
                    "text": JOB_LABELS.get(job.kind, job.kind.replace("_", " "))
                    + " · "
                    + job.state,
                    "detail": "Worker record",
                    "url": "/settings#operations",
                }
            )
        progress = []
        for campaign in session.scalars(select(Campaign).where(Campaign.active.is_(True))):
            members = select(CampaignLead.company_id).where(CampaignLead.campaign_id == campaign.id)
            counts = {
                "Found": session.scalar(
                    select(func.count())
                    .select_from(CampaignLead)
                    .where(CampaignLead.campaign_id == campaign.id)
                )
                or 0
            }
            for label, model in (("Enriched", Evidence), ("Audited", WebsiteAudit)):
                counts[label] = (
                    session.scalar(
                        select(func.count(func.distinct(model.company_id))).where(
                            model.company_id.in_(members)
                        )
                    )
                    or 0
                )
            for label in ("Drafted", "Approved", "Sent"):
                query = (
                    select(func.count(func.distinct(Conversation.company_id)))
                    .join(Draft, Draft.conversation_id == Conversation.id)
                    .where(Conversation.campaign_id == campaign.id)
                )
                if label == "Approved":
                    query = query.join(DraftApproval, DraftApproval.draft_id == Draft.id)
                elif label == "Sent":
                    query = query.join(SendIntent, SendIntent.draft_id == Draft.id).where(
                        SendIntent.state == "accepted"
                    )
                counts[label] = session.scalar(query) or 0
            progress.append({"campaign": campaign, "counts": counts})
    return {
        "activity_feed": sorted(feed, key=lambda row: row["at"], reverse=True)[:20],
        "campaign_progress": progress,
        "activity_checked": now,
    }
