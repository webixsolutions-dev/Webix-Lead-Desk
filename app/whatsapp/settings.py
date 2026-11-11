"""Owner-controlled pairing, connection rotation and immediate local disconnect."""

import secrets
from datetime import timedelta

from sqlalchemy import delete, update

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import token_hash
from app.core.vault import cipher
from app.db.delivery_models import IntegrationSecret
from app.db.models import AuditEvent, new_id
from app.db.session import Database
from app.db.whatsapp_models import WhatsAppConnection, WhatsAppNotice
from app.security.sessions import lock_user

KEY = "whatsapp_agent"


def configure(db: Database, settings: Settings, actor_id: str, key: str, details: bool) -> str:
    """Rotate the pairing and revoke old review codes atomically; return a one-time link code."""
    key = key.strip()
    if not 1 <= len(key) <= 4096 or any(c in key for c in "\r\n"):
        raise ValueError("Paste the API key from WhatsApp Settings → Agents")
    encrypted = cipher(settings).encrypt(key.encode()).decode()
    code = secrets.token_hex(8).upper()
    with db.sessions.begin() as session:
        actor = lock_user(session, actor_id)
        if actor.role != "owner":
            raise ValueError("Only the owner can connect WhatsApp")
        credential = session.get(IntegrationSecret, KEY)
        if credential is None:
            session.add(IntegrationSecret(key=KEY, ciphertext=encrypted, updated_by=actor_id))
        else:
            credential.ciphertext, credential.updated_by = encrypted, actor_id
            credential.updated_at = utc_now()
        item = session.get(WhatsAppConnection, "primary")
        if item is None:
            item = WhatsAppConnection(id="primary", owner_id=actor_id)
            session.add(item)
        item.owner_id, item.generation = actor_id, new_id()
        item.enabled, item.share_details = True, details
        item.recipient, item.pair_hash = "", token_hash(code)
        item.pair_expires = utc_now() + timedelta(minutes=15)
        item.offset, item.connected_at, item.last_poll = 0, None, None
        item.next_poll, item.status = utc_now(), "waiting_for_link"
        session.execute(update(WhatsAppNotice).values(action="revoked"))
        session.add(AuditEvent(actor_id=actor_id, action="whatsapp.configured"))
    return code


def disconnect(db: Database, actor_id: str) -> None:
    """Delete the stored API token and revoke all pending WhatsApp approvals."""
    with db.sessions.begin() as session:
        if lock_user(session, actor_id).role != "owner":
            raise ValueError("Only the owner can disconnect WhatsApp")
        item = session.get(WhatsAppConnection, "primary")
        if item:
            item.enabled, item.recipient, item.pair_hash = False, "", ""
            item.generation, item.status = new_id(), "disconnected"
        session.execute(delete(IntegrationSecret).where(IntegrationSecret.key == KEY))
        session.execute(update(WhatsAppNotice).values(action="revoked"))
        session.add(AuditEvent(actor_id=actor_id, action="whatsapp.disconnected"))
