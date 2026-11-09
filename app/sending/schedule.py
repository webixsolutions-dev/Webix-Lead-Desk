"""Owner-reviewed scheduling and bounded worker planning, without email side effects."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import IntegrationSecret, SendIntent
from app.db.models import AuditEvent, User, new_id
from app.db.outreach_models import Draft, DraftApproval, Mailbox
from app.db.session import Database
from app.drafting.service import approval_blocks
from app.jobs.queue import enqueue
from app.sending.window import next_window


def schedule(db: Database, draft_id: str, actor_id: str, now: datetime | None = None) -> SendIntent:
    """Reserve one approved business step; no recurring template approval is implied."""
    now = now or utc_now()
    with db.sessions() as session:
        actor = session.get(User, actor_id)
        draft = session.get(Draft, draft_id)
        approval = session.get(DraftApproval, draft_id)
        if not actor or not actor.active or actor.role != "owner":
            raise ValueError("Only the owner can schedule outreach")
        if (
            not draft
            or draft.state != "approved"
            or not approval
            or approval.digest != draft.digest
        ):
            raise ValueError("Approve the exact message before scheduling")
    reasons = approval_blocks(db, draft)
    if reasons:
        raise ValueError("; ".join(reasons))
    try:
        with db.sessions.begin() as session:
            existing = session.scalar(
                select(SendIntent).where(
                    SendIntent.conversation_id == draft.conversation_id,
                    SendIntent.step == draft.step,
                )
            )
            if existing:
                if existing.draft_id == draft_id:
                    if existing.state == "held" and existing.attempts == 0:
                        existing.state, existing.due_at, existing.hold_reason = (
                            "scheduled",
                            next_window(now),
                            "",
                        )
                    return existing
                if existing.state not in {"scheduled", "held"} or existing.attempts:
                    raise ValueError(
                        "This step was already attempted; it cannot be scheduled again"
                    )
                existing.draft_id, existing.approval_digest = draft_id, draft.digest
                existing.mailbox_id = draft.snapshot["mailbox_id"]
                existing.message_id = f"<{new_id()}@{draft.snapshot['from_email'].split('@')[1]}>"
                existing.state, existing.hold_reason = "scheduled", ""
                existing.wire_message = None
                return existing
            due = now
            if draft.step and not draft.snapshot.get("attachments"):
                first = session.scalar(
                    select(SendIntent).where(
                        SendIntent.conversation_id == draft.conversation_id,
                        SendIntent.step == draft.step - 1,
                        SendIntent.state == "accepted",
                    )
                )
                if not first or not first.accepted_at:
                    raise ValueError("Follow-up requires confirmed delivery of the previous step")
                due = max(now, first.accepted_at + timedelta(days=3))
            item = SendIntent(
                conversation_id=draft.conversation_id,
                draft_id=draft_id,
                mailbox_id=draft.snapshot["mailbox_id"],
                step=draft.step,
                due_at=next_window(due),
                message_id=f"<{new_id()}@{draft.snapshot['from_email'].split('@')[1]}>",
                approval_digest=draft.digest,
            )
            session.add(item)
            session.flush()
            session.add(
                AuditEvent(actor_id=actor_id, action="delivery.scheduled", entity_id=item.id)
            )
            return item
    except IntegrityError as error:
        raise ValueError("This step was scheduled concurrently; reload the outbox") from error


def plan_jobs(db: Database, settings: Settings, now: datetime) -> None:
    """Plan short inbox batches and due sends; a disabled worker never plans transmissions."""
    if not settings.encryption_key or settings.app_mode == "VERCEL":
        return
    with db.sessions() as session:
        mailboxes = list(session.scalars(select(Mailbox).where(Mailbox.enabled.is_(True))))
        ready = {box.id for box in mailboxes if session.get(IntegrationSecret, "mailbox:" + box.id)}
        due = list(
            session.scalars(
                select(SendIntent)
                .where(SendIntent.state == "scheduled", SendIntent.due_at <= now)
                .limit(100)
            )
        )
    for mailbox_id in ready:
        enqueue(
            db,
            f"inbox:{mailbox_id}:{int(now.timestamp()) // 300}",
            "inbox",
            {"mailbox_id": mailbox_id},
            now,
        )
    if settings.live_send_enabled:
        for item in due:
            if item.mailbox_id in ready:
                enqueue(
                    db,
                    f"send:{item.id}:{int(item.due_at.timestamp())}",
                    "send",
                    {"intent_id": item.id},
                    now,
                )
