"""Human call outcomes, exact verbal consent and company-wide outreach stops."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.calls.access import can_call
from app.core.clock import utc_now
from app.db.acquisition_models import CallRecord
from app.db.lead_models import (
    CampaignLead,
    ChannelReservation,
    Company,
    Contact,
    ContactBasis,
)
from app.db.models import User
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
            from app.linkedin.coordination import lock_company

            lock_company(session, company_id)
            user = session.get(User, actor)
            company = session.get(Company, company_id)
            if (
                not user
                or not user.active
                or not can_call(session, company_id, user)
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
            from app.linkedin.coordination import record_touch, stop_company

            record_touch(
                session,
                company_id,
                "call:" + item.id,
                "phone",
                "outbound",
                outcome,
                actor=actor,
                at=now,
            )
            if outcome not in {"no_answer", "voicemail"}:
                stop_company(
                    session, company_id, outcome, "phone", "call:" + item.id, actor=actor, at=now
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
