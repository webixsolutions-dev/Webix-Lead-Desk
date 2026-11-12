"""Owner-editable partner guidance and recorded local meeting capability."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lead_models import Campaign, Company
from app.db.outreach_models import LocalTeamMember, PlaybookVersion

PRIVATE_HOST_MARKETS = frozenset({"AE", "NZ"})

PARTNER_GUIDANCE = """
Write as a real named person from Webix Solutions using its Hostinger business mailbox, never
Camzio branding. Target Canadian/US IT companies, web/digital agencies and software firms for
white-label or overflow capacity. Start a conversation, not a hard sale.
Plain text, under 120 words, no links, attachments, prices or guarantees. Start with one
specific evidenced fact about their services, projects or stack. No flattery.
Offer relevant white-label WordPress/custom/e-commerce web builds, Flutter apps, CRM,
automation, SEO or design capacity. Discuss overnight delivery capacity without guaranteeing
every task by morning. Their brand remains on the work.
Use only owner-recorded Local team members for in-person meetings; name the matching person and
location. Without a match, offer video and remind the owner to add the local member. Do not
invent a Calgary representative.
Offer a small paid trial task, an NDA before client details, and one clear question such as a
short call next week. Never invent clients, results, team size or certifications outside
verified Proof Library facts.
Partner sequence: day 0 first contact; day 4 one relevant proof card; day 10 close the loop.
Each message has a different angle and its own owner approval."""


def seed_partner(session: Session, actor_id: str) -> None:
    """Seed one version only, preserving every subsequent owner edit."""
    if not session.scalar(select(PlaybookVersion.id).where(PlaybookVersion.scope == "partner")):
        session.add(
            PlaybookVersion(
                scope="partner", version=1, guidance=PARTNER_GUIDANCE, created_by=actor_id
            )
        )


def local_members(session: Session, company: Company, campaign: Campaign) -> list[LocalTeamMember]:
    """Prefer explicit city matches; country-wide availability must have an empty city."""
    return [
        m
        for m in session.scalars(
            select(LocalTeamMember).where(
                LocalTeamMember.active.is_(True), LocalTeamMember.country == campaign.country
            )
        )
        if not m.city or m.city.strip().casefold() == company.city.split(",")[0].strip().casefold()
    ]


def meeting_block(
    session: Session, company: Company, campaign: Campaign, body: str, *, kind: str = "reply"
) -> str | None:
    """Verify physical meeting offers and keep local hosts private during initial outreach."""
    private_host = campaign.country in PRIVATE_HOST_MARKETS
    if campaign.lane != "partner" and not private_host:
        return None
    members = local_members(session, company, campaign)
    opening = kind in {"first_touch", "follow_up"}
    named = any(m.name.casefold() in " ".join(body.split()).casefold() for m in members)
    if private_host and opening and named:
        return "Keep the local meeting host's name private until the recipient is interested"
    physical = re.search(
        r"in[- ]person|face[- ]to[- ]face|meet locally|local (?:team|meeting)"
        r"|visit your|on[- ]site",
        body,
        re.I,
    )
    if physical and (not members or (not named and not (private_host and opening))):
        return "Record and name a local team member for this location, or offer a video call"
    return None
