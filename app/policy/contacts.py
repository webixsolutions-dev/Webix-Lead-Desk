"""Global suppression and explicit first-touch channel reservations."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.lead_models import ChannelReservation, Company, Contact, SuppressionEntry
from app.db.session import Database
from app.sources.validation import domain_of, email_address


def suppression_key(value: str) -> str:
    """Normalize an email or domain into an unambiguous global stop key."""
    return "email:" + email_address(value) if "@" in value else "domain:" + domain_of(value)


def suppress(db: Database, value: str, reason: str) -> None:
    """Idempotently record an opt-out or owner stop across every campaign."""
    if not reason.strip() or len(reason) > 500:
        raise ValueError("Provide a reason up to 500 characters")
    key = suppression_key(value)
    if key in {"email:", "domain:"}:
        raise ValueError("Provide an address or domain")
    try:
        with db.sessions.begin() as session:
            if (
                session.scalar(select(SuppressionEntry.id).where(SuppressionEntry.key == key))
                is None
            ):
                session.add(SuppressionEntry(key=key, reason=reason.strip()))
    except IntegrityError:
        with db.sessions() as session:
            if (
                session.scalar(select(SuppressionEntry.id).where(SuppressionEntry.key == key))
                is None
            ):
                raise


def is_suppressed(db: Database, email: str, website: str = "") -> bool:
    """Check email and recipient/business domains, independent of the campaign."""
    email = email_address(email)
    keys = {"email:" + email}
    if email:
        keys.add("domain:" + email.rsplit("@", 1)[1])
    if website:
        keys.add("domain:" + domain_of(website))
    with db.sessions() as session:
        return (
            session.scalar(
                select(SuppressionEntry.id).where(SuppressionEntry.key.in_(keys)).limit(1)
            )
            is not None
        )


def reserve_channel(db: Database, company_id: str, channel: str) -> bool:
    """Atomically reserve email or manual form first touch, never both."""
    if channel not in {"email", "form", "call", "dm"}:
        raise ValueError("Unknown first-touch channel")
    try:
        with db.sessions.begin() as session:
            existing = session.get(ChannelReservation, company_id)
            if existing:
                return existing.channel == channel
            session.add(ChannelReservation(company_id=company_id, channel=channel))
        return True
    except IntegrityError:
        with db.sessions() as session:
            existing = session.get(ChannelReservation, company_id)
            return existing is not None and existing.channel == channel


def company_suppressed(db: Database, company_id: str) -> bool:
    """Apply global stops to a business even before it has a usable email address."""
    import re

    with db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            return True
        from app.db.linkedin_models import CompanyCoordination

        coordination = session.get(CompanyCoordination, company_id)
        if coordination and coordination.state in {"suppressed", "cooldown"}:
            return True
        keys = {"company:" + company_id}
        if company.phone:
            keys.add("phone:" + re.sub(r"\D", "", company.phone))
        if company.website:
            keys.add("domain:" + domain_of(company.website))
        for address in session.scalars(
            select(Contact.email).where(Contact.company_id == company_id)
        ):
            keys.update({"email:" + address, "domain:" + address.rsplit("@", 1)[1]})
        return (
            session.scalar(
                select(SuppressionEntry.id).where(SuppressionEntry.key.in_(keys)).limit(1)
            )
            is not None
        )
