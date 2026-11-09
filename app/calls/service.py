"""Human call outcomes, exact verbal consent and company-wide outreach stops."""

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.db.acquisition_models import CallRecord
from app.db.delivery_models import SendIntent
from app.db.lead_models import (
    CampaignLead,
    ChannelReservation,
    Company,
    Contact,
    ContactBasis,
    SuppressionEntry,
)
from app.db.models import User
from app.db.outreach_models import Conversation, Draft
from app.db.research_models import Evidence
from app.db.session import Database
from app.journey.events import emit
from app.policy.contacts import company_suppressed
from app.sources.validation import email_address

OUTCOMES = {
    "no_answer": "No answer",
    "voicemail": "Left voicemail",
    "not_interested": "Not interested",
    "callback": "Call back later",
    "interested": "Interested",
    "meeting_booked": "Meeting booked",
}


def record_call(
    db: Database,
    company_id: str,
    campaign_id: str,
    actor: str,
    request_key: str,
    outcome: str,
    notes: str,
    *,
    due_at: datetime | None = None,
    email: str = "",
    consent: bool = False,
    consent_note: str = "",
) -> CallRecord:
    """Record a real human outcome once; no API, dialer or message is invoked."""
    now = utc_now()
    with db.sessions() as session:
        existing = session.scalar(select(CallRecord).where(CallRecord.request_key == request_key))
        if existing:
            if (existing.company_id, existing.actor_id) != (company_id, actor):
                raise ValueError("Call request belongs to another record")
            return existing
    if (
        outcome not in OUTCOMES
        or not 2 <= len(notes.strip()) <= 4000
        or not 1 <= len(request_key) <= 128
    ):
        raise ValueError("Choose an outcome and add a short factual call note")
    if due_at and (due_at.tzinfo is None or due_at <= now):
        raise ValueError("Choose a future callback/meeting time in Calgary")
    if outcome in {"callback", "meeting_booked"} and not due_at:
        raise ValueError("A callback or meeting needs a date and time")
    email = email_address(email)
    if email and (not consent or not 10 <= len(consent_note.strip()) <= 2000):
        raise ValueError("Record the exact verbal permission before saving an agreed email")
    if outcome == "interested" and not email:
        raise ValueError("Capture the agreed email and explicit consent for Interested")
    if company_suppressed(db, company_id):
        raise ValueError(
            "This business is globally suppressed; do not call or start another sequence"
        )
    try:
        with db.sessions.begin() as session:
            user = session.get(User, actor)
            company = session.get(Company, company_id)
            if (
                not user
                or not user.active
                or not company
                or not company.phone
                or not session.scalar(
                    select(CampaignLead.id).where(
                        CampaignLead.company_id == company_id,
                        CampaignLead.campaign_id == campaign_id,
                    )
                )
            ):
                raise ValueError(
                    "An active staff member, business phone and matching campaign are required"
                )
            channel = session.get(ChannelReservation, company_id)
            if channel and channel.channel not in {"call", "email"}:
                raise ValueError("Another first-touch channel is already reserved")
            if not channel:
                channel = ChannelReservation(company_id=company_id, channel="call")
                session.add(channel)
            contact = None
            if email:
                contact = session.scalar(
                    select(Contact).where(Contact.company_id == company_id, Contact.email == email)
                )
                if not contact:
                    contact = Contact(company_id=company_id, email=email)
                    session.add(contact)
                    session.flush()
                contact.verification, contact.verified_at = "verified", now
                # Express consent is attributable and reviewed; DNS is still checked at approval.
                session.add(
                    ContactBasis(
                        contact_id=contact.id,
                        kind="express_consent",
                        source_url="",
                        relevance=consent_note.strip(),
                        no_solicitation=False,
                        reviewed_by=actor,
                        observed_at=now,
                        expires_at=now + timedelta(days=365),
                    )
                )
                channel.channel = "email"
            item = CallRecord(
                request_key=request_key,
                company_id=company_id,
                campaign_id=campaign_id,
                actor_id=actor,
                outcome=outcome,
                notes=notes.strip(),
                due_at=due_at,
                contact_id=contact.id if contact else None,
                consent_note=consent_note.strip() if email else "",
            )
            session.add(item)
            session.flush()
            session.add(
                Evidence(
                    company_id=company_id,
                    kind="human_call",
                    source_url="",
                    facts={"call_id": item.id, "notes": notes.strip(), "outcome": outcome},
                    reviewed_by=actor,
                )
            )
            if outcome == "not_interested":
                keys = {"company:" + company_id, "phone:" + re.sub(r"\D", "", company.phone)}
                keys.update(
                    "email:" + value
                    for value in session.scalars(
                        select(Contact.email).where(Contact.company_id == company_id)
                    )
                )
                for key in keys:
                    if not session.scalar(
                        select(SuppressionEntry.id).where(SuppressionEntry.key == key)
                    ):
                        session.add(
                            SuppressionEntry(key=key, reason="Not interested on a human call")
                        )
                conversation = session.scalar(
                    select(Conversation).where(Conversation.company_id == company_id)
                )
                if conversation:
                    conversation.state = "stopped"
                    for intent in session.scalars(
                        select(SendIntent).where(
                            SendIntent.conversation_id == conversation.id,
                            SendIntent.state.in_(["scheduled", "held"]),
                        )
                    ):
                        intent.state, intent.hold_reason = "cancelled", "call_not_interested"
                    for draft in session.scalars(
                        select(Draft).where(
                            Draft.conversation_id == conversation.id,
                            Draft.state.in_(["draft", "approved"]),
                        )
                    ):
                        draft.state = "cancelled"
                emit(
                    session,
                    company_id,
                    "call_stop:" + item.id,
                    "stopped",
                    {"reason": "Not interested on a human call"},
                    actor=actor,
                )
            if email:
                emit(
                    session,
                    company_id,
                    "consent:" + item.id,
                    "consent_recorded",
                    {
                        "contact_id": contact.id if contact else "",
                        "call_id": item.id,
                        "channel": "email",
                    },
                    actor=actor,
                )
            return item
    except IntegrityError as error:
        raise ValueError(
            "A concurrent call changed this lead; reload before saving again"
        ) from error
