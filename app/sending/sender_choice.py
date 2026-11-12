"""Owner-selected campaign mailboxes with consistent signature and thread routing."""

import re
from html import escape

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.lead_models import Campaign
from app.db.models import AuditEvent, User, WorkspaceSetting
from app.db.outreach_models import Conversation, Mailbox
from app.db.session import Database
from app.sending.campaign_sender import SENDER_KEY_PREFIX, selectable_sender
from app.sending.signature_html import clean_signature, plain_signature
from app.sending.signatures import CAMPAIGN_SIGNATURE_PREFIX


def save_campaign_sender(
    db: Database, campaign_id: str, mailbox_id: str, version: int, actor_id: str
) -> None:
    """Assign a profile before dispatch and move the signature's exact email with it."""
    try:
        with db.sessions.begin() as session:
            campaign = session.get(Campaign, campaign_id, with_for_update=True)
            actor = session.get(User, actor_id)
            mailbox = session.get(Mailbox, mailbox_id)
            key = SENDER_KEY_PREFIX + campaign_id
            current = session.get(WorkspaceSetting, key)
            requirement = current.value if current else {}
            if not actor or actor.role != "owner" or not actor.active:
                raise ValueError("Only an active owner can choose the campaign sender")
            if not campaign or not mailbox or not selectable_sender(campaign, mailbox, requirement):
                raise ValueError("Choose an eligible sender profile for this campaign")
            if version != (current.version if current else 0):
                raise SettingsConflict("Sender assignment changed. Reload the campaign.")
            email = mailbox.email.strip().casefold()
            if requirement.get("email") == email:
                return
            if campaign.active:
                raise ValueError("Pause the campaign before changing its sender")
            if session.scalar(
                select(Conversation.id)
                .where(
                    Conversation.campaign_id == campaign_id,
                    or_(Conversation.locked_at.is_not(None), Conversation.state != "draft"),
                )
                .limit(1)
            ):
                raise ValueError("Keep the original sender for reserved or existing email threads")
            now = utc_now()
            value = {
                **requirement,
                "email": email,
                "domain": email.rpartition("@")[2],
                "brand": mailbox.brand,
                "shared": mailbox.lane != campaign.lane or requirement.get("shared") is True,
            }
            if current:
                current.value, current.version, current.updated_at = value, version + 1, now
            else:
                session.add(WorkspaceSetting(key=key, value=value, version=1, updated_at=now))
            signature = session.get(WorkspaceSetting, CAMPAIGN_SIGNATURE_PREFIX + campaign_id)
            old_sender_name = str(requirement.get("sender_name", "")).strip().casefold()
            old_sender_email = str(requirement.get("email", ""))
            if not old_sender_name and old_sender_email:
                previous = session.scalar(select(Mailbox).where(Mailbox.email == old_sender_email))
                old_sender_name = previous.sender_name.strip().casefold() if previous else ""
            if (
                signature
                and signature.value.get("html")
                and signature.value.get("email") == old_sender_email
                and old_sender_name == mailbox.sender_name.strip().casefold()
            ):
                old_email = str(signature.value.get("email", ""))
                html = str(signature.value["html"])
                if old_email:
                    html = re.sub(
                        re.escape(old_email), lambda _: escape(email, quote=True), html, flags=re.I
                    )
                html = clean_signature(html)
                signature.value = {"email": email, "html": html, "text": plain_signature(html)}
                signature.version += 1
                signature.updated_at = now
            session.add(
                AuditEvent(actor_id=actor_id, action="campaign.sender_saved", entity_id=campaign_id)
            )
    except IntegrityError as error:
        raise SettingsConflict("Sender assignment changed. Reload the campaign.") from error
