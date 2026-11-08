"""Read-only deployment and pilot checks with truthful real-data dashboard counts."""

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import (
    InboxCursor,
    InboxMessage,
    IntegrationSecret,
    MailboxState,
    SendIntent,
)
from app.db.lead_models import Company
from app.db.models import Job, WorkerInstance
from app.db.outreach_models import Draft, Mailbox
from app.db.pilot_models import Pilot, PilotMember
from app.db.session import Database
from app.sending.policy import READINESS
from app.sending.window import CALGARY


def readiness(db: Database, settings: Settings) -> list[dict[str, str | bool]]:
    """Show configuration evidence without making DNS, mailbox or billing claims."""
    now = utc_now()
    with db.sessions() as session:
        worker = session.get(WorkerInstance, "primary")
        live_worker = bool(worker and worker.lease_expires_at > now)
        runtime = worker.runtime_config or {} if worker else {}
        boxes = list(session.scalars(select(Mailbox).where(Mailbox.enabled.is_(True))))
        configured = [box for box in boxes if session.get(IntegrationSecret, "mailbox:" + box.id)]
        verified = []
        fresh = []
        for box in configured:
            state = session.get(MailboxState, box.id)
            cursor = session.get(InboxCursor, box.id)
            if state and all(state.readiness.get(key) is True for key in READINESS):
                verified.append(box.id)
            if (
                cursor
                and cursor.synced_at
                and now - cursor.synced_at <= timedelta(minutes=15)
                and not cursor.error_code
            ):
                fresh.append(box.id)
        pilot = session.get(Pilot, "primary")
        member_count = session.scalar(select(func.count()).select_from(PilotMember)) or 0
        unknown = (
            session.scalar(
                select(func.count())
                .select_from(SendIntent)
                .where(SendIntent.state.in_(["uncertain", "transmitting"]))
            )
            or 0
        )
    return [
        {
            "label": "Workspace database",
            "ready": True,
            "detail": "PostgreSQL connected"
            if settings.database_url.startswith("postgresql")
            else "SQLite connected locally",
        },
        {
            "label": "Credential encryption",
            "ready": bool(settings.encryption_key),
            "detail": "Dedicated key configured"
            if settings.encryption_key
            else "Set ENCRYPTION_KEY privately on web and worker",
        },
        {
            "label": "Single worker",
            "ready": live_worker,
            "detail": "Worker heartbeat current"
            if live_worker
            else "Start the PC/VPS worker; keep the PC awake",
        },
        {
            "label": "Worker delivery switch",
            "ready": live_worker and runtime.get("live_send_enabled") is True,
            "detail": "Live delivery enabled on the worker"
            if live_worker and runtime.get("live_send_enabled")
            else "Live delivery remains off or unreported",
        },
        {
            "label": "Titan connection",
            "ready": bool(configured),
            "detail": f"{len(configured)} enabled connection(s); provider acceptance is separate",
        },
        {
            "label": "Sender acceptance",
            "ready": bool(verified),
            "detail": f"{len(verified)} owner-attested checklist(s); verify DNS separately",
        },
        {
            "label": "Inbox freshness",
            "ready": bool(set(fresh) & set(verified)),
            "detail": "A ready sender has a complete recent sync"
            if set(fresh) & set(verified)
            else "Complete inbox sync before dispatch",
        },
        {
            "label": "Reviewed pilot cohort",
            "ready": member_count == 10,
            "detail": f"{member_count}/10 reviewed: 4 HVAC, 4 roofing, 2 cleaning",
        },
        {
            "label": "Pilot launch review",
            "ready": bool(pilot and pilot.state in {"active", "complete"}),
            "detail": pilot.state if pilot else "Not reviewed yet",
        },
        {
            "label": "Uncertain transmissions",
            "ready": unknown == 0,
            "detail": f"{unknown} unresolved transmission(s); inspect Outbox before expanding",
        },
    ]


def metrics(db: Database) -> dict[str, Any]:
    """Count persisted records only, using the Calgary calendar day for accepted sends."""
    start = datetime.combine(utc_now().astimezone(CALGARY).date(), time(), CALGARY)
    with db.sessions() as session:
        return {
            "company_count": session.scalar(select(func.count()).select_from(Company)) or 0,
            "review_count": session.scalar(
                select(func.count()).select_from(Draft).where(Draft.state == "draft")
            )
            or 0,
            "sent_today": session.scalar(
                select(func.count())
                .select_from(SendIntent)
                .where(SendIntent.state == "accepted", SendIntent.accepted_at >= start)
            )
            or 0,
            "reply_count": session.scalar(select(func.count()).select_from(InboxMessage)) or 0,
            "held_count": session.scalar(
                select(func.count())
                .select_from(SendIntent)
                .where(SendIntent.state.in_(["held", "uncertain"]))
            )
            or 0,
            "failed_jobs": session.scalar(
                select(func.count()).select_from(Job).where(Job.state == "failed")
            )
            or 0,
            "review_drafts": list(
                session.scalars(
                    select(Draft)
                    .where(Draft.state == "draft")
                    .order_by(Draft.created_at.desc())
                    .limit(5)
                )
            ),
        }
