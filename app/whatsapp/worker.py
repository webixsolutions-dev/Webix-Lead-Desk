"""Bounded native-agent polling on the leased PC/VPS worker, never inside Vercel requests."""

from datetime import datetime, timedelta

from sqlalchemy import delete, select, update

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.models import Job, User
from app.db.outreach_models import Draft
from app.db.session import Database
from app.db.whatsapp_models import WhatsAppCommand, WhatsAppConnection, WhatsAppNotice
from app.jobs.queue import enqueue
from app.operations.control import maintenance_enabled
from app.whatsapp import transport
from app.whatsapp.commands import handle
from app.whatsapp.notices import collect
from app.whatsapp.settings import KEY


def plan(db: Database, settings: Settings, now: datetime) -> None:
    """Enqueue at most one poll; inactive integrations create no jobs and make no requests."""
    if not settings.encryption_key or settings.app_mode == "VERCEL" or not settings.public_base_url:
        return
    with db.sessions.begin() as session:
        connection = session.get(WhatsAppConnection, "primary")
        if not connection or not connection.enabled or connection.next_poll > now:
            return
        pending = session.scalar(
            select(Job.id)
            .where(
                Job.kind == "whatsapp",
                Job.state.in_(["queued", "running"]),
            )
            .limit(1)
        )
        if pending:
            return
        generation = connection.generation
        connection.next_poll = now + timedelta(seconds=20)
    enqueue(
        db,
        f"whatsapp:{generation}:{int(now.timestamp()) // 20}",
        "whatsapp",
        {"generation": generation},
        now,
    )


def dispatch(db: Database, settings: Settings, generation: str) -> str:
    """Poll once, handle commands and attempt one notice, preserving uncertain outcomes."""
    with db.sessions() as session:
        if maintenance_enabled(session):
            return "WhatsApp paused during maintenance."
        connection = session.get(WhatsAppConnection, "primary")
        if not connection or not connection.enabled or connection.generation != generation:
            return "WhatsApp disconnected."
        actor = session.get(User, connection.owner_id)
        if not actor or not actor.active or actor.role != "owner":
            return "WhatsApp owner unavailable."
        offset = connection.offset
    try:
        key = read_secret(db, settings, KEY)
        cursor, messages = transport.updates(key, offset)
        for message in messages:
            handle(db, settings, generation, message)
        with db.sessions.begin() as session:
            session.execute(
                update(WhatsAppConnection)
                .where(
                    WhatsAppConnection.id == "primary",
                    WhatsAppConnection.generation == generation,
                )
                .values(offset=cursor, last_poll=utc_now())
            )
        collect(db, settings, generation)
        deliver_one(db, key, generation)
    except (transport.AgentError, ValueError) as error:
        code = error.code if isinstance(error, transport.AgentError) else "credential_unavailable"
        with db.sessions.begin() as session:
            session.execute(
                update(WhatsAppConnection)
                .where(
                    WhatsAppConnection.generation == generation,
                )
                .values(status=code, next_poll=utc_now() + timedelta(minutes=5))
            )
        return "WhatsApp connection needs review; see settings."
    with db.sessions.begin() as session:
        item = session.get(WhatsAppConnection, "primary")
        if item and item.generation == generation:
            item.status = "connected" if item.recipient else "waiting_for_link"
        session.execute(
            delete(WhatsAppNotice).where(WhatsAppNotice.expires_at < utc_now() - timedelta(days=7))
        )
        session.execute(
            delete(WhatsAppCommand).where(
                WhatsAppCommand.created_at < utc_now() - timedelta(days=31)
            )
        )
    return "WhatsApp checked; commands and notifications recorded."


def deliver_one(db: Database, key: str, generation: str) -> None:
    """Mark in-flight before HTTP; crashes/timeouts never cause duplicate automatic notices."""
    with db.sessions.begin() as session:
        connection = session.get(WhatsAppConnection, "primary")
        if (
            not connection
            or not connection.enabled
            or connection.generation != generation
            or not connection.recipient
        ):
            return
        item = session.scalar(
            select(WhatsAppNotice)
            .where(
                WhatsAppNotice.generation == generation,
                WhatsAppNotice.state == "pending",
                WhatsAppNotice.expires_at > utc_now(),
            )
            .order_by((WhatsAppNotice.kind == "draft"), WhatsAppNotice.created_at)
            .limit(1)
        )
        if not item:
            return
        if item.draft_id:
            draft = session.get(Draft, item.draft_id)
            if not draft or draft.state != "draft" or draft.digest != item.digest:
                item.state = "stale"
                return
        claimed = session.scalar(
            update(WhatsAppNotice)
            .where(
                WhatsAppNotice.id == item.id,
                WhatsAppNotice.state == "pending",
            )
            .values(state="sending")
            .returning(WhatsAppNotice.id)
        )
        if not claimed:
            return
        item_id, recipient, body = item.id, connection.recipient, item.body
    try:
        mid = transport.send(key, recipient, body)
    except transport.AgentError:
        with db.sessions.begin() as session:
            session.execute(
                update(WhatsAppNotice).where(WhatsAppNotice.id == item_id).values(state="uncertain")
            )
        raise
    with db.sessions.begin() as session:
        session.execute(
            update(WhatsAppNotice)
            .where(WhatsAppNotice.id == item_id)
            .values(state="sent", message_id=mid)
        )
