"""Reviewed listing evidence for businesses with no confirmed website."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.lead_models import CampaignLead, Company
from app.db.research_models import Evidence
from app.db.session import Database
from app.journey.events import emit
from app.scoring.service import LABELS, score
from app.sources.validation import website_url


class ListingReview(BaseModel):
    """Explicit human observations; a blank field always means unknown."""

    model_config = ConfigDict(extra="forbid")
    website_status: str = Field(pattern="^(true|false|unknown)$")
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0, le=10_000_000)
    photos: str = Field(default="", max_length=500)
    hours: str = Field(default="", max_length=500)
    recent_posts: str = Field(default="", max_length=500)
    social_activity: str = Field(default="", max_length=500)
    note: str = Field(min_length=10, max_length=2000)
    source_url: str = Field(max_length=2000)
    signals: dict[str, bool] = Field(default_factory=dict)


def review_listing(db: Database, company_id: str, review: ListingReview, actor: str) -> Evidence:
    """Save source-backed observations and recalculate every campaign score."""
    source = website_url(review.source_url)
    if not source or set(review.signals) - LABELS.keys():
        raise ValueError("Choose a public source and supported scoring signals")
    signals: dict[str, Any] = dict(review.signals)
    if review.website_status != "unknown":
        signals["no_website"] = review.website_status == "false"
    if review.review_count is not None:
        signals["reviews"] = review.review_count >= 10
    with db.sessions.begin() as session:
        company = session.get(Company, company_id)
        if not company:
            raise ValueError("Business not found")
        if review.website_status == "false" and company.website:
            raise ValueError(
                "A website is already recorded; correct its identity before confirming no website"
            )
        company.has_website = review.website_status
        facts = review.model_dump(exclude={"source_url", "signals"})
        facts["signals"] = signals
        item = Evidence(
            company_id=company_id, kind="listing", source_url=source, facts=facts, reviewed_by=actor
        )
        session.add(item)
        session.flush()
        emit(
            session,
            company_id,
            "listing:" + item.id,
            "audited",
            {"state": "listing_reviewed", "reason": review.note, "evidence_id": item.id},
            actor=actor,
        )
        campaigns = list(
            session.scalars(
                select(CampaignLead.campaign_id).where(CampaignLead.company_id == company_id)
            )
        )
    for campaign in campaigns:
        score(db, company_id, campaign)
    return item
