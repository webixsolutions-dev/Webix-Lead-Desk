"""Internal bundle suggestions tied to an assessed need, never first-touch pitches."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.outreach_models import Offer
from app.db.research_models import Assessment


def matching_bundles(session: Session, company_id: str, campaign_id: str) -> list[dict[str, Any]]:
    """Show why a bundle might fit without claiming every component is required."""
    assessment = session.scalar(
        select(Assessment)
        .where(Assessment.company_id == company_id, Assessment.campaign_id == campaign_id)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    if not assessment or assessment.result.get("decision") not in {"recommend", "ask_first"}:
        return []
    service = str(assessment.result.get("service", ""))
    result = []
    for offer in session.scalars(select(Offer).where(Offer.offer_type == "bundle")):
        if offer.status == "paused" or service.casefold() not in {
            x.casefold() for x in offer.components
        }:
            continue
        result.append(
            {
                "id": offer.id,
                "name": offer.name,
                "status": offer.status,
                "components": offer.components,
                "scope": offer.description,
                ("reason"): f"The assessment identified {service}: {
                    assessment.result.get(('reason'), (''))
                }",
                ("caution"): (
                    "Confirm each component during discovery. No price or "
                    "implementation has been agreed."
                ),
            }
        )
    return result[:3]


# Templates are installed explicitly once; later owner edits are never overwritten.
BUNDLE_TEMPLATES = [
    (
        "Get Online",
        ["Websites and redesign", "Google Business Profile"],
        (
            "Website launch and local profile foundations. Confirm domain, "
            "pages, content, profile eligibility and access. Excludes "
            "hosting, licences and ranking promises."
        ),
    ),
    (
        "Business Growth",
        ["Landing pages and CRO", "CRM and sales pipelines", "Workflow automation and AI agents"],
        (
            "Enquiry journey, lead pipeline and a scoped workflow. Confirm "
            "lead sources, handoffs, integrations and ongoing ownership. "
            "Excludes ad spend and usage charges."
        ),
    ),
    (
        "Local Lead Engine",
        ["Google Business Profile", "SEO and AI search visibility", "Paid advertising"],
        (
            "Local search foundations with a separately scoped campaign. "
            "Confirm service areas, tracking, budget and approval process. No "
            "lead or ranking guarantees; ad spend is separate."
        ),
    ),
    (
        "Smart Front Desk",
        ["AI voice agents", "CRM and sales pipelines", "Client portals and custom software"],
        (
            "Discovery for inbound call handling, booking and staff access. "
            "Feasibility, real calendar access, privacy, escalation and "
            "integration costs must be reviewed before a delivery offer. "
            "Telecom and API usage are separate."
        ),
    ),
    (
        "Content Engine",
        ["Social media management", "Content and copywriting", "Video and ad creative"],
        (
            "Scoped content calendar, reviewed copy and video creative. "
            "Confirm channels, asset rights, production format, revisions and "
            "publishing access. No invented testimonials or performance "
            "guarantees."
        ),
    ),
]


def install_templates(session: Session, actor_id: str) -> int:
    """Install five internal planning templates idempotently, with no quoted amounts."""
    from app.db.models import AuditEvent, User

    owner = session.get(User, actor_id)
    if not owner or not owner.active or owner.role != "owner":
        raise ValueError("Only the owner can install offer templates")
    count = 0
    for index, (name, components, scope) in enumerate(BUNDLE_TEMPLATES, 1):
        key = f"cc270000-0000-4000-8000-{index:012d}"
        if not session.get(Offer, key):
            session.add(
                Offer(
                    id=key,
                    service=name,
                    name=name,
                    description=scope,
                    offer_type="bundle",
                    components=components,
                    availability="discovery_only",
                    active=False,
                    pricing_mode="quote",
                )
            )
            count += 1
    session.add(
        AuditEvent(actor_id=actor_id, action="offers.bundle_templates", details={"added": count})
    )
    session.flush()
    return count
