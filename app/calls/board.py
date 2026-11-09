"""Mobile/print call cards built from reviewed facts and configured offers."""

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.acquisition_models import CallNote, CallRecord, LeadScore
from app.db.lead_models import (
    Campaign,
    CampaignLead,
    ChannelReservation,
    Company,
    Contact,
    ContactBasis,
)
from app.db.outreach_models import Offer
from app.db.research_models import Evidence
from app.db.session import Database
from app.linkedin.coordination import outreach_block
from app.policy.contacts import company_suppressed
from app.sources.enrichment.config import get_config


def offer_for_listing(db: Database) -> Offer | None:
    """Use only an owner-selected active Offer Library entry; never invent a package."""
    config, _ = get_config(db)
    with db.sessions() as session:
        offer = (
            session.get(Offer, config.no_website_offer_id) if config.no_website_offer_id else None
        )
        return offer if offer and offer.active else None


def call_list(db: Database) -> list[dict[str, Any]]:
    """Return eligible due human calls, ordered by their latest evidence-based score."""
    now = utc_now()
    offer = offer_for_listing(db)
    cards: list[dict[str, Any]] = []
    with db.sessions() as session:
        companies = list(session.scalars(select(Company).where(Company.phone != "")))
        for company in companies:
            if company_suppressed(db, company.id):
                continue
            campaign = session.scalar(
                select(Campaign)
                .join(CampaignLead)
                .where(CampaignLead.company_id == company.id, Campaign.active.is_(True))
                .order_by(Campaign.created_at)
            )
            if not campaign:
                continue
            channel = session.get(ChannelReservation, company.id)
            if channel and channel.channel in {"form", "dm"}:
                continue
            usable = session.scalar(
                select(Contact.id)
                .join(ContactBasis)
                .where(
                    Contact.company_id == company.id,
                    Contact.verification == "verified",
                    Contact.verified_at > now - timedelta(days=30),
                    ContactBasis.expires_at > now,
                    ContactBasis.no_solicitation.is_(False),
                )
            )
            last = session.scalar(
                select(CallRecord)
                .where(CallRecord.company_id == company.id)
                .order_by(CallRecord.created_at.desc())
                .limit(1)
            )
            if outreach_block(session, company.id) and not (last and last.outcome == "callback"):
                continue
            if last and (
                last.outcome in {"interested", "meeting_booked", "not_interested"}
                or (last.due_at and last.due_at > now)
                or (not last.due_at and last.created_at > now - timedelta(days=1))
            ):
                continue
            if (
                usable
                and company.has_website != "false"
                and not (last and last.outcome == "callback")
            ):
                continue
            score = session.scalar(
                select(LeadScore)
                .where(LeadScore.company_id == company.id)
                .order_by(LeadScore.created_at.desc())
                .limit(1)
            )
            facts = list(
                session.scalars(
                    select(Evidence)
                    .where(Evidence.company_id == company.id, Evidence.reviewed_by.is_not(None))
                    .order_by(Evidence.observed_at.desc())
                    .limit(6)
                )
            )
            no_site = company.has_website == "false"
            service = (
                offer.service
                if no_site and offer
                else campaign.services[0]
                if not no_site and campaign.services
                else "Choose a reviewed offer in Settings"
            )
            question = (
                "Would a free homepage concept be useful to review?"
                if no_site and offer
                else "How do you currently handle new enquiries outside opening hours?"
            )
            opener = (
                "Hi, I'm [your real name] from [the configured business identity]. "
                f"Is now a good time for one quick question about {company.name}? "
            )
            opener += (
                "I had a question about how customers find your services online. "
                if no_site and offer
                else "I'd like to understand how new customer enquiries reach your team. "
            )
            opener += question
            cards.append(
                {
                    "company": company,
                    "campaign": campaign,
                    "score": score,
                    "facts": facts,
                    "summary": f"{company.industry} in {company.city}"
                    + (" · No website confirmed" if no_site else ""),
                    "question": question,
                    "opener": opener,
                    "offer": service,
                    "best_time": last.due_at.astimezone(ZoneInfo("America/Edmonton")).strftime(
                        "%d %b, %I:%M %p Calgary"
                    )
                    if last and last.due_at
                    else "Suggested: weekdays 9:30–11:30 AM Calgary; confirm published hours",
                    "call_key": f"call:{company.id}:{now.isoformat()}",
                    "manual_dm": f"Hi {company.name}, {question}" if no_site and offer else "",
                }
            )
    return sorted(
        cards, key=lambda item: item["score"].points if item["score"] else -1, reverse=True
    )


def call_history(db: Database, limit: int = 30) -> list[dict[str, Any]]:
    """Link recent human notes, model suggestions and unapproved draft records."""
    with db.sessions() as session:
        return [
            {
                "call": call,
                "company": session.get(Company, call.company_id),
                "cleaned": session.scalar(select(CallNote).where(CallNote.call_id == call.id)),
            }
            for call in session.scalars(
                select(CallRecord).order_by(CallRecord.created_at.desc()).limit(limit)
            )
        ]
