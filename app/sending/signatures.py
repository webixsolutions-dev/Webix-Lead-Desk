"""Versioned mailbox signatures and campaign overrides."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.lead_models import Campaign
from app.db.models import AuditEvent, User, WorkerInstance, WorkspaceSetting
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.sending.campaign_sender import SENDER_KEY_PREFIX, campaign_from_name
from app.sending.signature_html import clean_signature, plain_signature

CAMPAIGN_SIGNATURE_PREFIX = "campaign_signature:"
CAMPAIGN_SIGNATURE_CAPABILITY = "campaign_signatures"


def signature_key(mailbox_id: str) -> str:
    """Namespace one mailbox's non-secret signature without a schema migration."""
    return "mailbox_signature:" + mailbox_id


def _snapshot(item: WorkspaceSetting | None) -> dict[str, Any]:
    return {
        "html": item.value.get("html", "") if item else "",
        "text": item.value.get("text", "") if item else "",
        "version": item.version if item else 0,
    }


def get_signature(
    session: Session, mailbox_id: str, *, campaign_id: str | None = None
) -> dict[str, Any]:
    """Prefer a campaign's saved signature for its exact assigned sender."""
    if campaign_id:
        mailbox = session.get(Mailbox, mailbox_id)
        from_name = campaign_from_name(session, campaign_id, mailbox) if mailbox else ""
        override = session.get(WorkspaceSetting, CAMPAIGN_SIGNATURE_PREFIX + campaign_id)
        if (
            override
            and override.value.get("html")
            and mailbox
            and mailbox.email.casefold() == override.value.get("email")
            and override.value.get("from_name", mailbox.sender_name) == from_name
        ):
            return _snapshot(override)
        if mailbox and from_name != mailbox.sender_name:
            return _snapshot(None)
    return _snapshot(session.get(WorkspaceSetting, signature_key(mailbox_id)))


def require_campaign_signature_worker(session: Session) -> None:
    """Require a worker that can reproduce the effective approved signature."""
    worker = session.get(WorkerInstance, "primary")
    if (
        not worker
        or worker.lease_expires_at <= utc_now()
        or (worker.runtime_config or {}).get(CAMPAIGN_SIGNATURE_CAPABILITY) != 1
    ):
        raise ValueError("Start worker v0.30.7 or newer to use a campaign signature for sending")


def save_signature(
    db: Database,
    target_id: str,
    html: str,
    version: int,
    actor_id: str,
    *,
    campaign_override: bool = False,
    sender_version: int | None = None,
) -> None:
    """Save sanitized owner content with optimistic concurrency and a payload-free audit."""
    cleaned = clean_signature(html)
    value = {"html": cleaned, "text": plain_signature(cleaned)}
    action = "campaign.signature_saved" if campaign_override else "mailbox.signature_saved"
    if cleaned and not value["text"]:
        raise ValueError("Include the sender's readable name and contact details")
    try:
        with db.sessions.begin() as session:
            actor = session.get(User, actor_id)
            if not actor or not actor.active or actor.role != "owner":
                raise ValueError("Only an active owner can change a signature")
            if campaign_override:
                campaign = session.get(Campaign, target_id, with_for_update=True)
                sender = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + target_id)
                email = str(sender.value.get("email", "")).strip().casefold() if sender else ""
                if not campaign or not email:
                    raise ValueError("Choose a campaign with an assigned sender address")
                if sender_version is not None and sender and sender.version != sender_version:
                    raise SettingsConflict("Campaign sender changed. Review the signature again.")
                if campaign.active:
                    require_campaign_signature_worker(session)
                value["email"] = email
                if sender and sender.value.get("from_name"):
                    value["from_name"] = str(sender.value["from_name"])
                key = CAMPAIGN_SIGNATURE_PREFIX + target_id
            else:
                if not session.get(Mailbox, target_id):
                    raise ValueError("Choose an existing mailbox")
                key = signature_key(target_id)
            if version == 0:
                session.add(WorkspaceSetting(key=key, value=value, version=1, updated_at=utc_now()))
                session.flush()
            else:
                changed = session.scalar(
                    update(WorkspaceSetting)
                    .where(WorkspaceSetting.key == key, WorkspaceSetting.version == version)
                    .values(value=value, version=version + 1, updated_at=utc_now())
                    .returning(WorkspaceSetting.key)
                )
                if not changed:
                    raise SettingsConflict(
                        "Signature changed in another tab. Reload before saving."
                    )
            session.add(
                AuditEvent(
                    actor_id=actor_id,
                    action=action,
                    entity_id=target_id,
                    details={"version": version + 1},
                )
            )
    except IntegrityError as error:
        raise SettingsConflict("Signature changed in another tab. Reload before saving.") from error
