"""Database adapters for exact-recipient conversation review."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.outreach_models import Offer
from app.drafting.checks import review_copy
from app.drafting.context import email_context, linkedin_context


def catalogue(session: Session) -> list[dict[str, Any]]:
    """Describe statuses without giving the model internal prices."""
    return [
        {
            "id": o.id,
            "service": o.service,
            "name": o.name,
            "status": o.status,
            "offer_type": o.offer_type,
        }
        for o in session.scalars(select(Offer).order_by(Offer.id))
    ]


def email_report(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """Use the same mailbox/contact boundary as the message being reviewed."""
    return review_copy(
        str(data["body"]),
        email_context(session, data["company_id"], data["contact_id"], data["mailbox_id"]),
        channel="email",
        action=data.get("kind", "first_touch"),
        offers=catalogue(session),
        allow_price=data.get("allow_price") is True,
    )


def linkedin_report(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """LinkedIn history remains manually recorded and sender-specific."""
    return review_copy(
        str(data["body"]),
        linkedin_context(session, data["person_id"], data["profile_id"]),
        channel="linkedin",
        action=data["action"],
        offers=catalogue(session),
        allow_price=data.get("allow_price") is True,
    )


def stale_check(saved: dict[str, Any], current: dict[str, Any]) -> bool:
    """An exact review cannot silently survive changed context or offer availability."""
    return bool(saved and saved.get("context_digest") != current["context_digest"])


def first_touch_check(session: Session, body: str) -> None:
    """Keep named bundles and unavailable delivery promises out of first-contact copy."""
    report = review_copy(body, {}, channel="email", action="first_touch", offers=catalogue(session))
    reasons = [
        f["reason"] for f in report["flags"] if f["code"] in {"bundle_pitch", "unavailable_promise"}
    ]
    if reasons:
        raise ValueError("; ".join(reasons))
