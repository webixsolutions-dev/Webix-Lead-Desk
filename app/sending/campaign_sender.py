"""Optional workspace requirements for the sender identity on a campaign."""

from sqlalchemy.orm import Session

from app.db.models import WorkspaceSetting
from app.db.outreach_models import Mailbox

SENDER_KEY_PREFIX = "campaign_sender:"
WORKER_CAPABILITY = "campaign_sender_policy"


def sender_matches(session: Session, campaign_id: str, mailbox: Mailbox) -> bool:
    """Reject a mailbox that differs from the campaign's required brand or domain."""
    requirement = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + campaign_id)
    if requirement is None:
        return True
    return (
        mailbox.email.rpartition("@")[2].casefold() == requirement.value.get("domain")
        and mailbox.brand.strip().casefold() == str(requirement.value.get("brand", "")).casefold()
    )
