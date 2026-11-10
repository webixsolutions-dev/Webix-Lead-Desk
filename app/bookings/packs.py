"""Prepare a concise meeting brief from stored, attributable observations only."""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.booking_models import Booking
from app.db.lead_models import Company, Contact
from app.db.outreach_models import Conversation, Draft
from app.db.proposal_models import MeetingPack
from app.db.research_models import Assessment, Evidence
from app.drafting.context import email_context
from app.playbook.bundles import matching_bundles
from app.playbook.industries import context


def prepare(session: Session, booking: Booking) -> MeetingPack:
    """Snapshot one page; include messages only for an exact recorded email match."""
    company = session.get(Company, booking.company_id)
    if not company:
        raise ValueError("Business not found")
    conversation = session.scalar(
        select(Conversation)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(
            Conversation.company_id == company.id,
            Contact.email == booking.contact.strip().casefold(),
        )
    )
    audit_query = select(Assessment).where(Assessment.company_id == company.id)
    if conversation:
        audit_query = audit_query.where(Assessment.campaign_id == conversation.campaign_id)
    audit = session.scalar(audit_query.order_by(Assessment.created_at.desc()).limit(1))
    facts = list(
        session.scalars(
            select(Evidence)
            .where(Evidence.company_id == company.id, Evidence.reviewed_by.is_not(None))
            .order_by(Evidence.observed_at.desc())
            .limit(3)
        )
    )
    messages: list[dict[str, str]] = []
    opening = ""
    if conversation:
        history = email_context(
            session, company.id, conversation.contact_id, conversation.mailbox_id
        )
        messages = history["recent_messages_data_only"]
        first = session.scalar(
            select(Draft)
            .where(
                Draft.conversation_id == conversation.id,
                Draft.kind == "first_touch",
                Draft.state == "sent",
            )
            .order_by(Draft.created_at)
            .limit(1)
        )
        opening = str(first.snapshot.get("body", ""))[:650] if first else ""
    industry = context(session, company.industry)
    questions: list[str] = []
    cautions: list[str] = []
    for record in industry:
        for line in record["guidance"].splitlines():
            if "**Opening" in line or "**Discovery" in line:
                questions.extend(re.findall(r'["“]([^"”]+\?)["”]', line))
            if "**Caution" in line:
                cautions.append(re.sub(r"\*\*", "", line.lstrip("- "))[:280])
    data: dict[str, Any] = {
        "company": company.name,
        "website": company.website,
        "industry": company.industry,
        "location": company.city,
        "contact": booking.contact,
        "host": booking.host,
        "conversation_id": conversation.id if conversation else "",
        "audit": str(audit.result.get("reason", ""))[:450] if audit else "No assessment recorded.",
        "audit_status": "Owner reviewed" if audit and audit.reviewed_by else "Needs owner review",
        "facts": [
            {
                "source": f.source_url,
                "text": str(f.facts.get("note", f.kind))[:180],
                "observed_at": f.observed_at.isoformat(),
            }
            for f in facts
        ],
        "opening": opening,
        "reply": next(
            (m["text"][:450] for m in reversed(messages) if m["direction"] == "inbound"), ""
        ),
        "questions": questions[:3]
        or ["What would you like to improve?", "How does that work today?"],
        "industry_status": industry[0]["status"] if industry else "No industry match",
        "bundles": matching_bundles(session, company.id, audit.campaign_id)[:1] if audit else [],
        "cautions": cautions[:1]
        + [
            "Confirm the need, access, integrations and costs before committing scope or dates.",
            "No guaranteed results, invented local availability or unapproved prices.",
        ],
    }
    pack = MeetingPack(booking_id=booking.id, booking_version=booking.version, snapshot=data)
    session.add(pack)
    return pack
