"""Bounded plain-text LinkedIn copy and reviewed contact-basis validation."""

import re
from datetime import datetime
from typing import Any

from app.db.linkedin_models import LinkedInProfile
from app.db.outreach_models import Mailbox
from app.drafting.content import check_copy, links


def identity(profile: LinkedInProfile, mailbox: Mailbox) -> str:
    """Use a configured business identity, with the real member's display name."""
    if not all(
        x.strip() for x in (mailbox.brand, mailbox.mailing_address, mailbox.contact_details)
    ):
        raise ValueError("Configure the business identity, mailing address and contact details")
    return (
        f"{profile.display_name} | {mailbox.brand}\n{mailbox.mailing_address}\n"
        f"{mailbox.contact_details}\nTo stop these messages, reply stop."
    )


def validate_copy(action: str, body: str, noncommercial: bool) -> None:
    """Keep invitations non-promotional and all message stages free of price promises."""
    if action == "withdraw":
        if body:
            raise ValueError("Withdrawal has no message text")
        return
    if action == "invite":
        if not noncommercial or len(body) > 200 or any(c in body for c in "\x00<>"):
            raise ValueError("Confirm a non-commercial invitation of at most 200 characters")
        if body:
            check_copy("Invitation", body, first_touch=True)
        if re.search(
            r"\b(?:offer|sell|sale|services?|discount|book a call|free audit|pitch)\b", body, re.I
        ):
            raise ValueError("Use a blank or genuinely non-promotional connection invitation")
        return
    if action not in {"message1", "message2", "message3", "reply"}:
        raise ValueError("Unsupported message action")
    check_copy("LinkedIn message", body, first_touch=action == "message1")
    if action == "message1" and not 150 <= len(body) <= 200:
        raise ValueError("Keep the first message body between 150 and 200 characters")
    if action in {"message2", "message3"} and len(body) > 700:
        raise ValueError("Keep follow-ups within 700 characters")
    if action == "message3" and links(body):
        raise ValueError("The closing message should not add another link")


def validate_basis(basis: dict[str, Any], now: datetime) -> None:
    """Require a human-reviewed basis for this LinkedIn recipient, never infer from email."""
    if basis.get("kind") not in {"express_consent", "existing_relationship", "public_publication"}:
        raise ValueError("Review a contact basis specifically applicable to LinkedIn messaging")
    if not 20 <= len(str(basis.get("note", ""))) <= 2000:
        raise ValueError("Record the evidence and why this LinkedIn contact basis applies")
    try:
        expiry = datetime.fromisoformat(str(basis.get("expires_at", "")))
    except ValueError as error:
        raise ValueError("Choose a valid contact-basis expiry") from error
    if expiry.tzinfo is None or expiry <= now:
        raise ValueError("The reviewed contact basis is expired")
    if basis.get("kind") == "public_publication" and not str(basis.get("source", "")).startswith(
        "https://"
    ):
        raise ValueError("Public publication needs its source and a role-relevance review")
