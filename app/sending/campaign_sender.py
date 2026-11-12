"""Eligibility rules for campaign sender identities."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.lead_models import Campaign
from app.db.models import WorkspaceSetting
from app.db.outreach_models import Mailbox
from app.sending.providers import PERSONAL_DOMAINS, PRESETS

SENDER_KEY_PREFIX = "campaign_sender:"
WORKER_CAPABILITY = "campaign_sender_policy"
WORKER_POLICY_VERSION = 3


def selectable_sender(campaign: Campaign, mailbox: Mailbox, requirement: dict[str, Any]) -> bool:
    """Keep mailbox choices within the campaign's brand, lane and named identity."""
    domain = mailbox.email.rpartition("@")[2].casefold()
    brand = mailbox.brand.strip().casefold()
    expected_brand = str(requirement.get("brand", "")).strip().casefold()
    name = str(requirement.get("sender_name", "")).strip().casefold()
    return (
        mailbox.provider in PRESETS
        and (mailbox.lane == campaign.lane or requirement.get("shared") is True)
        and (not expected_brand or brand == expected_brand)
        and (not name or mailbox.sender_name.strip().casefold() == name)
        and (
            campaign.lane != "partner"
            or (
                brand == "webix solutions"
                and mailbox.provider in {"hostinger", "gmail"}
                and bool(domain)
                and domain not in PERSONAL_DOMAINS
            )
        )
    )


def sender_matches(session: Session, campaign: Campaign, mailbox: Mailbox) -> bool:
    """Apply an exact assignment when present, or the campaign's existing lane rules."""
    setting = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + campaign.id)
    requirement = setting.value if setting else {}
    if not selectable_sender(campaign, mailbox, requirement):
        return False
    if not setting:
        return True
    email = str(requirement.get("email", "")).strip().casefold()
    return (
        (mailbox.lane == campaign.lane or bool(email))
        and (not email or mailbox.email.casefold() == email)
        and mailbox.email.rpartition("@")[2].casefold() == requirement.get("domain")
    )
