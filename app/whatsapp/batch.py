"""Approve only the exact campaign drafts already delivered to the paired owner."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.db.models import AuditEvent, WorkspaceSetting
from app.db.outreach_models import Conversation, Draft
from app.db.session import Database
from app.db.whatsapp_models import WhatsAppConnection, WhatsAppNotice
from app.drafting.service import approve
from app.sending.schedule import schedule

KEY = "whatsapp_campaign_review"
LIMIT = 50


def policy(session: Session) -> tuple[str, datetime, datetime, datetime] | None:
    """Require an explicitly scoped, expiring review session and a scheduled start."""
    item = session.get(WorkspaceSetting, KEY)
    if not item:
        return None
    try:
        campaign = str(item.value["campaign_id"])
        start = datetime.fromisoformat(item.value["starts_at"])
        end = datetime.fromisoformat(item.value["expires_at"])
        due = datetime.fromisoformat(item.value["send_not_before"])
        if start <= utc_now() < end and due >= start and campaign:
            return campaign, start, due, end
    except (KeyError, ValueError, TypeError):
        pass
    return None


def claim(
    session: Session, connection: WhatsAppConnection, when: datetime
) -> list[tuple[str, str, str]]:
    """Claim current first-contact revisions acknowledged before the approval message."""
    active = policy(session)
    if not active or not connection.share_details:
        return []
    campaign, start, _, _ = active
    items = list(
        session.scalars(
            select(WhatsAppNotice)
            .join(Draft, Draft.id == WhatsAppNotice.draft_id)
            .join(Conversation, Conversation.id == Draft.conversation_id)
            .where(
                WhatsAppNotice.generation == connection.generation,
                WhatsAppNotice.kind == "draft",
                WhatsAppNotice.state == "sent",
                WhatsAppNotice.message_id != "",
                WhatsAppNotice.action == "",
                WhatsAppNotice.created_at >= start,
                WhatsAppNotice.expires_at > utc_now(),
                Draft.state == "draft",
                Draft.kind == "first_touch",
                Draft.digest == WhatsAppNotice.digest,
                Conversation.campaign_id == campaign,
                select(AuditEvent.id)
                .where(
                    AuditEvent.action == "whatsapp.notice_sent",
                    AuditEvent.entity_id == WhatsAppNotice.id,
                    AuditEvent.created_at < when,
                )
                .exists(),
            )
            .order_by(WhatsAppNotice.created_at, WhatsAppNotice.id)
            .limit(LIMIT)
        )
    )
    claimed: list[tuple[str, str, str]] = []
    for item in items:
        if item.draft_id and session.scalar(
            update(WhatsAppNotice)
            .where(WhatsAppNotice.id == item.id, WhatsAppNotice.action == "")
            .values(action="processing")
            .returning(WhatsAppNotice.id)
        ):
            claimed.append((item.id, item.draft_id, item.digest))
    return claimed


def schedule_time(session: Session, draft_id: str) -> datetime:
    """Apply the requested earliest time to individual and batch approvals alike."""
    now = utc_now()
    active = policy(session)
    draft = session.get(Draft, draft_id)
    if active and draft and draft.snapshot.get("campaign_id") == active[0]:
        return max(now, active[2])
    return now


def finish(
    db: Database, claimed: list[tuple[str, str, str]], actor_id: str, due: datetime
) -> tuple[int, int]:
    """Run ordinary eligibility checks and report the exact number scheduled."""
    scheduled = 0
    for notice_id, draft_id, digest in claimed:
        outcome = "needs_review"
        try:
            approve(db, draft_id, digest, actor_id)
            schedule(db, draft_id, actor_id, now=max(utc_now(), due))
            outcome = "approved"
            scheduled += 1
        except ValueError:
            pass
        with db.sessions.begin() as session:
            item = session.get(WhatsAppNotice, notice_id)
            if item:
                item.action = outcome
            session.add(
                AuditEvent(actor_id=actor_id, action="whatsapp." + outcome, entity_id=draft_id)
            )
    return scheduled, len(claimed) - scheduled
