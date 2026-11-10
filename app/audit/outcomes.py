"""Owner-recorded next actions reuse the same evidence validation as AI assessments."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.classify import Recommendation, validate_recommendation
from app.db.lead_models import Campaign, CampaignLead
from app.db.outreach_models import Offer
from app.db.research_models import Assessment, Evidence


def save_outcome(session: Session, company_id: str, actor: str, fields: dict[str, str]) -> None:
    """Record a new reviewed outcome; never rewrite historical assessments or send a message."""
    campaign = session.get(Campaign, fields.get("campaign_id", ""))
    if (
        not campaign
        or not campaign.active
        or not session.scalar(
            select(CampaignLead.id).where(
                CampaignLead.company_id == company_id, CampaignLead.campaign_id == campaign.id
            )
        )
    ):
        raise ValueError("Choose an active campaign for this business")
    if fields.get("verified") != "true":
        raise ValueError("Confirm the source facts and uncertainty")
    evidence = list(session.scalars(select(Evidence).where(Evidence.company_id == company_id)))
    offers = list(
        session.scalars(
            select(Offer).where(Offer.offer_type == "service", Offer.service.in_(campaign.services))
        )
    )
    services = [o.service for o in offers if o.status in {"available", "discovery_only"}]
    data: dict[str, Any] = {
        "decision": fields.get("decision"),
        "service": fields.get("service", "").strip(),
        "score": int(fields.get("score", "5")),
        "reason": fields.get("reason", "").strip(),
        "owner_question": fields.get("owner_question", "").strip()
        or "Review the recorded next action before contacting this business.",
        "observed": [
            {
                "evidence_id": fields["evidence_id"],
                "observation": fields.get("observation", "").strip(),
            }
        ]
        if fields.get("evidence_id")
        else [],
        "needs_confirmation": [
            x.strip() for x in fields.get("needs_confirmation", "").splitlines() if x.strip()
        ],
        "research_checks": [
            x.strip() for x in fields.get("research_checks", "").splitlines() if x.strip()
        ],
        "revisit_on": fields.get("revisit_on", ""),
    }
    result = validate_recommendation(
        Recommendation.model_validate(data),
        evidence,
        services,
        discovery_services=[o.service for o in offers if o.status == "discovery_only"],
    )
    session.add(
        Assessment(
            company_id=company_id,
            campaign_id=campaign.id,
            origin="manual",
            result=result.model_dump(),
            reviewed_by=actor,
        )
    )


def may_draft(session: Session, company_id: str, campaign_id: str) -> bool:
    """Do not enqueue a message for a research/revisit/no-opportunity result."""
    latest = session.scalar(
        select(Assessment)
        .where(Assessment.company_id == company_id, Assessment.campaign_id == campaign_id)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    return bool(latest and latest.result.get("decision") in {"recommend", "ask_first"})
