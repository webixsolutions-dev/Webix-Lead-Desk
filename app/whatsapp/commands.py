"""Deterministic owner commands; model output and quoted business text never authorize sends."""

import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import token_hash
from app.db.models import AuditEvent, User
from app.db.outreach_models import Draft
from app.db.session import Database
from app.db.whatsapp_models import WhatsAppCommand, WhatsAppConnection, WhatsAppNotice
from app.drafting.service import approve
from app.sending.schedule import schedule
from app.whatsapp import batch
from app.whatsapp.notices import add_notice

REACTION_ACTIONS = {"👍": "APPROVE", "👎": "REJECT", "✏️": "EDIT"}


def handle(db: Database, settings: Settings, generation: str, message: dict[str, Any]) -> None:
    """Consume each provider message once and accept only explicit, current-owner commands."""
    mid, sender = message.get("id"), message.get("from")
    payload = message.get("text")
    text = (
        payload.get("body", "")
        if message.get("type") == "text" and isinstance(payload, dict)
        else ""
    )
    reaction = message.get("reaction")
    if message.get("type") != "reaction" or not isinstance(reaction, dict):
        reaction = {}
    if not isinstance(mid, str) or not 1 <= len(mid) <= 300 or not isinstance(text, str):
        return
    if not isinstance(sender, str) or not re.fullmatch(r"user:[0-9]{1,80}", sender):
        return
    try:
        when = datetime.fromtimestamp(int(message.get("timestamp", "")), UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return
    now = utc_now()
    if when > now + timedelta(minutes=1) or when < now - timedelta(hours=24):
        return
    command_id = token_hash(generation + ":" + mid)
    if text.strip().upper() == "APPROVE":
        approve_batch(db, generation, sender, command_id, when)
        return
    try:
        with db.sessions.begin() as session:
            # This write serializes duplicate consumers before any approval side effect.
            session.add(WhatsAppCommand(id=command_id))
            session.flush()
            connection = session.get(WhatsAppConnection, "primary")
            if not connection or connection.generation != generation or not connection.enabled:
                return
            actor = session.get(User, connection.owner_id)
            if not actor or not actor.active or actor.role != "owner":
                return
            if not connection.recipient:
                if (
                    connection.pair_expires
                    and connection.pair_expires > now
                    and hmac.compare_digest(
                        token_hash(text.strip().removeprefix("LINK ")), connection.pair_hash
                    )
                ):
                    connection.recipient, connection.connected_at = sender, now
                    connection.pair_hash, connection.status = "", "connected"
                    add_notice(
                        session,
                        connection,
                        "paired",
                        "status",
                        "Webix Lead Desk connected. Alerts arrive while your worker runs. "
                        "For a draft notification, react 👍 to approve, 👎 to reject or "
                        "✏️ to edit. Typed commands remain available. Replies always need "
                        "your approval.",
                    )
                    session.add(AuditEvent(actor_id=actor.id, action="whatsapp.paired"))
                return
            if sender != connection.recipient:
                return
            match = re.fullmatch(r"(APPROVE|REJECT|EDIT) ([A-F0-9]{16})", text.strip())
            emoji = reaction.get("emoji")
            parent_id = reaction.get("message_id")
            reaction_action = REACTION_ACTIONS.get(emoji) if isinstance(emoji, str) else None
            if not match and (
                not reaction_action
                or not isinstance(parent_id, str)
                or not 1 <= len(parent_id) <= 300
            ):
                # No LLM interpretations, quoted text or arbitrary emoji.
                return
            if match:
                action, code = match.groups()
                notice = session.scalar(
                    select(WhatsAppNotice).where(
                        WhatsAppNotice.code == code,
                        WhatsAppNotice.generation == generation,
                        WhatsAppNotice.kind == "draft",
                        WhatsAppNotice.state == "sent",
                        WhatsAppNotice.action == "",
                        WhatsAppNotice.expires_at > now,
                    )
                )
            else:
                action = reaction_action
                notice = session.scalar(
                    select(WhatsAppNotice).where(
                        WhatsAppNotice.message_id == parent_id,
                        WhatsAppNotice.message_id != "",
                        WhatsAppNotice.generation == generation,
                        WhatsAppNotice.kind == "draft",
                        WhatsAppNotice.state == "sent",
                        WhatsAppNotice.action == "",
                        WhatsAppNotice.expires_at > now,
                    )
                )
            if not notice or not notice.draft_id or when < notice.created_at - timedelta(seconds=5):
                add_notice(
                    session,
                    connection,
                    "command:" + command_id,
                    "status",
                    "That action is expired, already used or unavailable. Review the current "
                    f"draft in {settings.public_base_url}/outbox",
                )
                return
            draft = session.get(Draft, notice.draft_id)
            if not draft or draft.state != "draft" or draft.digest != notice.digest:
                notice.action = "stale"
                add_notice(
                    session,
                    connection,
                    "command:" + command_id,
                    "status",
                    "This draft changed. Review the latest version in the dashboard.",
                )
                return
            if action == "EDIT":
                add_notice(
                    session,
                    connection,
                    "command:" + command_id,
                    "status",
                    f"Edit or rewrite, then review the new version:\n{settings.public_base_url}"
                    f"/leads/{draft.snapshot['company_id']}/draft?step={draft.step}&kind={draft.kind}",
                )
                return
            # Claim before side effects: a crash requires dashboard review, never a second action.
            notice.action = "processing"
            draft_id, digest, actor_id, notice_id = draft.id, notice.digest, actor.id, notice.id
    except IntegrityError:
        return
    try:
        if action == "APPROVE":
            with db.sessions() as session:
                due = batch.schedule_time(session, draft_id)
            approve(db, draft_id, digest, actor_id)
            schedule(db, draft_id, actor_id, now=due)
            outcome = "approved"
            reply = (
                "Approved and scheduled. Existing sending checks, limits and worker controls apply."
            )
        else:
            with db.sessions.begin() as session:
                changed = session.scalar(
                    update(Draft)
                    .where(
                        Draft.id == draft_id,
                        Draft.state == "draft",
                        Draft.digest == digest,
                    )
                    .values(state="rejected")
                    .returning(Draft.id)
                )
                if not changed:
                    raise ValueError("Draft changed")
                session.add(
                    AuditEvent(
                        actor_id=actor_id,
                        action="draft.rejected",
                        entity_id=draft_id,
                        details={"source": "whatsapp"},
                    )
                )
            outcome, reply = "rejected", "Draft rejected. It will not be sent."
    except ValueError:
        outcome = "needs_review"
        reply = (
            "This action needs dashboard review; no sending checks were bypassed. "
            f"Check the current approval/schedule state at {settings.public_base_url}/outbox"
        )
    with db.sessions.begin() as session:
        notice = session.get(WhatsAppNotice, notice_id)
        connection = session.get(WhatsAppConnection, "primary")
        if notice:
            notice.action = outcome
        session.add(AuditEvent(actor_id=actor_id, action="whatsapp." + outcome, entity_id=draft_id))
        if connection and connection.generation == generation and connection.enabled:
            add_notice(session, connection, "command:" + command_id, "status", reply)


def approve_batch(
    db: Database, generation: str, sender: str, command_id: str, when: datetime
) -> None:
    """Use one explicit command to approve the campaign drafts already shown in chat."""
    try:
        with db.sessions.begin() as session:
            connection = session.get(WhatsAppConnection, "primary")
            active = batch.policy(session)
            if (
                not connection
                or not connection.enabled
                or connection.generation != generation
                or connection.recipient != sender
                or not active
            ):
                return
            actor = session.get(User, connection.owner_id)
            if not actor or not actor.active or actor.role != "owner":
                return
            session.add(WhatsAppCommand(id=command_id))
            session.flush()
            candidates = batch.claim(session, connection, when)
            actor_id = actor.id
    except IntegrityError:
        return
    scheduled, blocked = batch.finish(db, candidates, actor_id, active[2])
    with db.sessions.begin() as session:
        connection = session.get(WhatsAppConnection, "primary")
        if connection and connection.enabled and connection.generation == generation:
            add_notice(
                session,
                connection,
                "command:" + command_id,
                "status",
                f"Campaign review: {scheduled} scheduled, {blocked} need review. "
                "Only current drafts already shown before your approval were included.",
            )
