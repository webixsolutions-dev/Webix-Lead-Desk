"""Versioned per-mailbox signatures in the existing non-secret settings store."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.models import AuditEvent, User, WorkspaceSetting
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.sending.signature_html import clean_signature, plain_signature


def signature_key(mailbox_id: str) -> str:
    """Namespace one mailbox's non-secret signature without a schema migration."""
    return "mailbox_signature:" + mailbox_id


def get_signature(session: Session, mailbox_id: str) -> dict[str, Any]:
    """Read one immutable snapshot of the current signature, including its revision."""
    item = session.get(WorkspaceSetting, signature_key(mailbox_id))
    return {
        "html": item.value.get("html", "") if item else "",
        "text": item.value.get("text", "") if item else "",
        "version": item.version if item else 0,
    }


def save_signature(db: Database, mailbox_id: str, html: str, version: int, actor_id: str) -> None:
    """Save sanitized owner content with optimistic concurrency and a payload-free audit."""
    cleaned = clean_signature(html)
    value = {"html": cleaned, "text": plain_signature(cleaned)}
    if cleaned and not value["text"]:
        raise ValueError("Include the sender's readable name and contact details")
    try:
        with db.sessions.begin() as session:
            actor = session.get(User, actor_id)
            if not actor or not actor.active or actor.role != "owner":
                raise ValueError("Only an active owner can change a mailbox signature")
            if not session.get(Mailbox, mailbox_id):
                raise ValueError("Choose an existing mailbox")
            key = signature_key(mailbox_id)
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
                    action="mailbox.signature_saved",
                    entity_id=mailbox_id,
                    details={"version": version + 1},
                )
            )
    except IntegrityError as error:
        raise SettingsConflict("Signature changed in another tab. Reload before saving.") from error
