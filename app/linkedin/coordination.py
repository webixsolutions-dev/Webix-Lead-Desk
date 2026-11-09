"""Shared company stops and append-only cross-channel touch evidence."""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.delivery_models import SendIntent
from app.db.form_models import FormDraft
from app.db.lead_models import Company, Contact, SuppressionEntry
from app.db.linkedin_models import CompanyCoordination, LinkedInTask, Person, Touch
from app.db.models import new_id
from app.db.outreach_models import Conversation, Draft
from app.journey.events import emit
from app.sources.validation import domain_of


def lock_company(session: Session, company_id: str) -> CompanyCoordination:
    """Take a portable write lock before a compound company policy decision."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    session.execute(
        insert(CompanyCoordination)
        .values(company_id=company_id, revision=0, state="active", reason="", updated_at=utc_now())
        .on_conflict_do_nothing(index_elements=["company_id"])
    )
    session.execute(
        update(CompanyCoordination)
        .where(CompanyCoordination.company_id == company_id)
        .values(revision=CompanyCoordination.revision + 1, updated_at=utc_now())
    )
    item = session.get(CompanyCoordination, company_id, populate_existing=True)
    assert item
    return item


def outreach_block(session: Session, company_id: str, *, response: bool = False) -> str | None:
    """Block new sequences after any reply and enforce permanent cross-channel stops."""
    company = session.get(Company, company_id)
    if not company:
        return "Business not found"
    keys = {"company:" + company_id}
    if company.website:
        keys.add("domain:" + domain_of(company.website))
    for email in session.scalars(select(Contact.email).where(Contact.company_id == company_id)):
        keys.update({"email:" + email, "domain:" + email.rsplit("@", 1)[-1]})
    for person in session.scalars(select(Person).where(Person.company_id == company_id)):
        keys.add("person:" + person.id)
        if person.linkedin_url:
            keys.add("linkedin:" + person.linkedin_url)
        if person.personal_email:
            keys.add("email:" + person.personal_email)
    if session.scalar(select(SuppressionEntry.id).where(SuppressionEntry.key.in_(keys)).limit(1)):
        return "Global suppression prevents outreach"
    item = session.get(CompanyCoordination, company_id)
    if item and item.state != "active":
        if response and item.state == "replied":
            return None  # Only a separately approved human response, never a new sequence.
        # Expiry permits owner review of a new angle; it never auto-resumes old work.
        return "Company outreach paused: " + item.reason
    return None


def cancel_pending(session: Session, company_id: str, reason: str, *, email: bool = True) -> None:
    """Cancel queued actions in the same transaction that records the inbound evidence."""
    for task in session.scalars(
        select(LinkedInTask).where(
            LinkedInTask.company_id == company_id, LinkedInTask.state.not_in(["done", "cancelled"])
        )
    ):
        task.state, task.reason = "cancelled", reason
    if not email:
        return
    for conversation in session.scalars(
        select(Conversation).where(Conversation.company_id == company_id)
    ):
        if conversation.state != "stopped":
            conversation.state = "replied"
        for intent in session.scalars(
            select(SendIntent).where(
                SendIntent.conversation_id == conversation.id,
                SendIntent.state.in_(["scheduled", "held", "transmitting"]),
            )
        ):
            intent.state, intent.hold_reason = "cancelled", "cross_channel_reply"
        for draft in session.scalars(
            select(Draft).where(
                Draft.conversation_id == conversation.id,
                Draft.state.in_(["draft", "approved", "sending"]),
            )
        ):
            draft.state = "cancelled"
    for form in session.scalars(
        select(FormDraft).where(
            FormDraft.company_id == company_id, FormDraft.state.in_(["draft", "approved"])
        )
    ):
        form.state = "cancelled"


def stop_company(
    session: Session,
    company_id: str,
    outcome: str,
    channel: str,
    key: str,
    *,
    actor: str | None = None,
    at: datetime | None = None,
) -> None:
    """Pause every company sequence; distinguish a reply, a 12-month no, and opt-out."""
    from app.linkedin.identity import end_ownership

    now = at or utc_now()
    item = lock_company(session, company_id)
    permanent = outcome in {"unsubscribe", "stop"}
    if item.state == "suppressed":
        permanent = True
    if permanent:
        item.state, item.hold_until = "suppressed", None
    elif outcome == "not_interested":
        item.state, item.hold_until = "cooldown", now + timedelta(days=365)
    elif item.state != "cooldown":
        item.state, item.hold_until = "replied", None
    item.reason = f"{channel}: {outcome.replace('_', ' ').capitalize()}"
    if permanent:
        keys = {"company:" + company_id}
        for person in session.scalars(select(Person).where(Person.company_id == company_id)):
            keys.add("person:" + person.id)
            if person.linkedin_url:
                keys.add("linkedin:" + person.linkedin_url)
            if person.personal_email:
                keys.add("email:" + person.personal_email)
        for value in keys:
            if not session.scalar(select(SuppressionEntry.id).where(SuppressionEntry.key == value)):
                session.add(SuppressionEntry(key=value, reason=item.reason))
    cancel_pending(session, company_id, item.reason)
    end_ownership(session, company_id)
    from app.db.linkedin_queue_models import LinkedInSequence

    session.execute(
        update(LinkedInSequence)
        .where(
            LinkedInSequence.person_id.in_(select(Person.id).where(Person.company_id == company_id))
        )
        .values(state="replied", hold_until=item.hold_until if item.state == "cooldown" else None)
    )
    emit(
        session,
        company_id,
        "cross_stop:" + key,
        "stopped"
        if permanent
        else "outreach_paused"
        if item.state == "cooldown"
        else "reply_received",
        {
            "reason": item.reason,
            "channel": channel,
            "hold_until": item.hold_until.isoformat() if item.hold_until else "",
        },
        actor=actor,
        at=now,
    )


def record_touch(
    session: Session,
    company_id: str,
    key: str,
    channel: str,
    direction: str,
    outcome: str,
    *,
    person_id: str | None = None,
    profile_id: str | None = None,
    actor: str | None = None,
    text: str = "",
    at: datetime | None = None,
) -> None:
    """Insert one fact and linked journal entry; content never grants execution authority."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    now = at or utc_now()
    session.execute(
        insert(Touch)
        .values(
            id=new_id(),
            event_key=key,
            company_id=company_id,
            person_id=person_id,
            profile_id=profile_id,
            channel=channel,
            direction=direction,
            outcome=outcome,
            text=text[:8000],
            actor_id=actor,
            occurred_at=now,
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
    )
    emit(
        session,
        company_id,
        "touch:" + key,
        "replied" if direction == "inbound" else "touch_recorded",
        {"person_id": person_id, "channel": channel, "outcome": outcome},
        actor=actor,
        at=now,
    )
