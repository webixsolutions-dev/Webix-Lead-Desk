"""Owner-editable partner guidance and recorded local meeting capability."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lead_models import Campaign, Company
from app.db.outreach_models import LocalTeamMember, PlaybookVersion

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
        if not m.city or m.city.casefold() == company.city.casefold()
    ]


def meeting_block(session: Session, company: Company, campaign: Campaign, body: str) -> str | None:
    """Require a recorded person before a partner draft promises a physical meeting."""
    if campaign.lane != "partner":
        return None
    if re.search(r"in[- ]person|meet locally|local team|visit your|on[- ]site", body, re.I):
        members = local_members(session, company, campaign)
        if not members or not any(m.name.casefold() in body.casefold() for m in members):
            return "Record and name a local team member for this location, or offer a video call"
    return None
