"""Optional workspace requirements for the sender identity on a campaign."""

from sqlalchemy.orm import Session

from app.db.lead_models import Campaign
from app.db.models import WorkspaceSetting
from app.db.outreach_models import Mailbox

SENDER_KEY_PREFIX = "campaign_sender:"
WORKER_CAPABILITY = "campaign_sender_policy"
WORKER_POLICY_VERSION = 2


def sender_matches(session: Session, campaign: Campaign, mailbox: Mailbox) -> bool:
    """Require the assigned lane or an explicitly shared, exact business address."""
    same_lane = mailbox.lane == campaign.lane
    requirement = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + campaign.id)
    if requirement is None:
        return same_lane
    email = str(requirement.value.get("email", "")).strip().casefold()
    return (
        (same_lane or (bool(email) and requirement.value.get("shared") is True))
        and (not email or mailbox.email.casefold() == email)
        and mailbox.email.rpartition("@")[2].casefold() == requirement.value.get("domain")
        and mailbox.brand.strip().casefold() == str(requirement.value.get("brand", "")).casefold()
    )
