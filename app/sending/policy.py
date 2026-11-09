"""Dispatch eligibility is rechecked independently of owner approval."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.delivery_models import InboxCursor, MailboxState, SendIntent
from app.db.models import Job, User, WorkerInstance
from app.db.outreach_models import Conversation, Draft, DraftApproval, Mailbox
from app.db.session import Database
from app.drafting.service import approval_blocks
from app.operations.control import maintenance_enabled
from app.pilot.service import gate
from app.sending.providers import readiness_fields
from app.sending.sequences import earliest_followup
from app.sending.window import within_window

READINESS = ("spf", "dkim", "dmarc", "privacy", "provider_limits", "test_accepted")


def claim_valid(session: Session, job: Job, now: datetime) -> bool:
    """Fence a stale process using both its job claim and the singleton worker lease."""
    current = session.get(Job, job.id)
    worker = session.get(WorkerInstance, "primary")
    return bool(
        not maintenance_enabled(session)
        and current
        and worker
        and current.state == "running"
        and job.claim_token
        and current.claim_token == job.claim_token
        and current.claim_owner == job.claim_owner == worker.owner_id
        and current.lease_expires_at
        and current.lease_expires_at > now
        and worker.lease_expires_at > now
    )


def delivery_blocks(
    db: Database, settings: Settings, item: SendIntent, now: datetime, *, transmitting: bool = False
) -> list[str]:
    """Recheck the exact approval, current sender, suppression, thread and inbox freshness."""
    reasons: list[str] = []
    if not settings.live_send_enabled or settings.app_mode == "VERCEL":
        reasons.append("worker_delivery_disabled")
    if not within_window(now):
        reasons.append("outside_calgary_window")
    with db.sessions() as session:
        draft = session.get(Draft, item.draft_id)
        approval = session.get(DraftApproval, item.draft_id)
        actor = session.get(User, approval.approved_by) if approval else None
        conversation = session.get(Conversation, item.conversation_id)
        mailbox = session.get(Mailbox, item.mailbox_id)
        state = session.get(MailboxState, item.mailbox_id)
        cursor = session.get(InboxCursor, item.mailbox_id)
        expected = "sending" if transmitting else "approved"
        if (
            not draft
            or draft.state != expected
            or not approval
            or not actor
            or not actor.active
            or actor.role != "owner"
            or not (approval.digest == draft.digest == item.approval_digest)
        ):
            reasons.append("exact_approval_required")
        if draft and (
            item.mailbox_id != draft.snapshot["mailbox_id"]
            or item.conversation_id != draft.snapshot["conversation_id"]
            or item.step != draft.snapshot["step"]
            or draft.kind != draft.snapshot.get("kind", draft.kind)
            or draft.sequence_step != draft.snapshot.get("sequence_step", draft.sequence_step)
        ):
            reasons.append("scheduled_routing_changed")
        if draft and draft.kind == "follow_up":
            try:
                if now < earliest_followup(session, draft):
                    reasons.append("followup_interval_or_predecessor")
            except ValueError:
                reasons.append("followup_interval_or_predecessor")
        if draft:
            reasons.extend(approval_blocks(db, draft))
        if (
            not mailbox
            or not mailbox.enabled
            or mailbox.provider not in {"titan", "hostinger", "gmail"}
            or not state
            or not all(state.readiness.get(key) is True for key in readiness_fields(mailbox))
        ):
            reasons.append("mailbox_not_ready")
        if state and state.paused_until and state.paused_until > now:
            reasons.append("mailbox_paused")
        if (
            not cursor
            or not cursor.synced_at
            or cursor.error_code
            or now - cursor.synced_at > timedelta(minutes=15)
        ):
            reasons.append("inbox_not_fresh")
        if (
            conversation
            and draft
            and item.step
            and (
                not conversation.first_message_id
                or draft.snapshot["in_reply_to"] != conversation.last_message_id
                or draft.snapshot["references"] != [conversation.first_message_id]
            )
        ):
            reasons.append("thread_context_changed")
        if conversation and (pilot_block := gate(session, conversation.company_id, item.step)):
            reasons.append(pilot_block)
    return reasons
