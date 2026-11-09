"""Stored automatic address checks, separate from contact permission and provenance."""

from datetime import timedelta

from app.core.clock import utc_now
from app.db.lead_models import Contact
from app.db.session import Database
from app.integrations.dns_mail import check_mail_route
from app.sources.validation import email_address

ROLE_ADDRESSES = {
    "admin",
    "support",
    "contact",
    "office",
    "hello",
    "team",
    "billing",
    "marketing",
    "service",
    "help",
    "accounts",
    "customerservice",
}


def address_block(contact: Contact) -> str | None:
    """Reject non-contact inboxes and gate other role addresses on owner review."""
    if not email_address(contact.email):
        return "Address syntax is invalid"
    local = contact.email.split("@", 1)[0].split("+", 1)[0].lower()
    if local.replace("-", "").replace("_", "") in {"noreply", "donotreply"}:
        return "No-reply addresses cannot receive outreach"
    if local in ROLE_ADDRESSES and not contact.allow_role:
        return "Owner must allow this role address; info@ and sales@ are allowed"
    return None


def verification_block(contact: Contact) -> str | None:
    """Require a recent positive automatic check bound to this exact address."""
    if reason := address_block(contact):
        return reason
    if (
        contact.dns_address != contact.email
        or not contact.dns_checked_at
        or contact.dns_checked_at < utc_now() - timedelta(days=30)
    ):
        return "Automatic email check is missing or older than 30 days"
    if contact.dns_result not in {"mx", "implicit_mx"}:
        return "Automatic mail routing check failed: " + (contact.dns_result or "unknown")
    return None


def verify_address(db: Database, contact_id: str) -> None:
    """Refresh syntax/DNS evidence before approval without sending an SMTP probe."""
    with db.sessions() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if reason := address_block(contact):
            raise ValueError(reason)
        if verification_block(contact) is None:
            return
        address = contact.email
    result = check_mail_route(address)
    with db.sessions.begin() as session:
        contact = session.get(Contact, contact_id)
        if not contact or contact.email != address:
            raise ValueError("Contact changed during verification")
        contact.dns_address, contact.dns_result, contact.dns_checked_at = address, result, utc_now()
    if result not in {"mx", "implicit_mx"}:
        raise ValueError("Automatic mail routing check failed: " + result)
