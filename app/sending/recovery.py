"""A crashed SMTP job is held, never recovered as a retryable email."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.delivery_models import SendAttempt, SendIntent
from app.db.models import Job
from app.db.outreach_models import Conversation, Draft


def hold_expired_send(session: Session, job: Job, now: datetime) -> None:
    """Fence a vanished worker while retaining its original sender and message identity."""
    item = session.get(SendIntent, str(job.payload.get("intent_id", "")))
    if not item or item.state != "transmitting":
        return
    item.state, item.hold_reason = "uncertain", "worker_lost_during_send"
    draft = session.get(Draft, item.draft_id)
    conversation = session.get(Conversation, item.conversation_id)
    if draft:
        draft.state = "uncertain"
    if conversation and conversation.state in {"draft", "active"}:
        conversation.state = "held"
    for attempt in session.scalars(
        select(SendAttempt).where(
            SendAttempt.intent_id == item.id, SendAttempt.outcome == "started"
        )
    ):
        attempt.outcome, attempt.finished_at = "uncertain", now
        attempt.error_code = "worker_lease_expired"
