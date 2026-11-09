"""Link existing transactional records into the append-only lead journal."""

from sqlalchemy import event, select
from sqlalchemy.orm import Session, UOWTransaction

from app.db.acquisition_models import CallRecord
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.lead_models import Campaign, Company, SourceRecord
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation, Draft, DraftApproval
from app.db.pipeline_models import DemoVersion, Opportunity, PipelineEvent
from app.db.research_models import Assessment, WebsiteAudit
from app.journey.events import emit


def conversation_company(session: Session, conversation_id: str) -> str | None:
    """Resolve a durable thread to its business without copying routing state."""
    return session.scalar(select(Conversation.company_id).where(Conversation.id == conversation_id))


def capture_intent(session: Session, item: SendIntent) -> None:
    """Journal scheduling, transmission and cancellation with immutable wire identifiers."""
    company = conversation_company(session, item.conversation_id)
    if not company:
        return
    draft = session.get(Draft, item.draft_id)
    sequence = draft.sequence_step if draft else item.step
    from app.sending.sequences import days_for

    conversation = session.get(Conversation, item.conversation_id)
    campaign = session.get(Campaign, conversation.campaign_id) if conversation else None
    kind = {
        "scheduled": "scheduled",
        "accepted": "followup_sent" if draft and draft.kind == "follow_up" else "sent",
        "cancelled": "followup_cancelled" if sequence else "cancelled",
        "held": "held",
        "uncertain": "held",
        "sending": "sending",
    }.get(item.state)
    if not kind:
        return
    emit(
        session,
        company,
        f"intent:{item.id}:{item.state}:{item.attempts}:{item.due_at.isoformat()}",
        kind,
        {
            "mailbox_id": item.mailbox_id,
            "message_id": item.message_id,
            "step": sequence,
            "followup_total": len(days_for(campaign)) if campaign else 4,
            "reason": item.hold_reason,
            "due_at": item.due_at.isoformat(),
            "intent_id": item.id,
            "channel": "email",
            "draft_id": item.draft_id,
            "message_kind": draft.kind if draft else "",
        },
        at=item.accepted_at if item.state == "accepted" else None,
        url=f"/drafts/{item.draft_id}",
    )


def capture_record(session: Session, item: object, *, new: bool = True) -> None:
    """Mirror one source record inside its own transaction; never inspect message bodies."""
    if isinstance(item, Company) and new:
        emit(
            session,
            item.id,
            "company:" + item.id,
            "found",
            {"source": "import"},
            at=item.created_at,
        )
    elif isinstance(item, SourceRecord) and new:
        emit(
            session,
            item.company_id,
            "source:" + item.id,
            "source_found",
            {"source": item.source, "source_url": item.source_url},
            at=item.observed_at,
        )
    elif isinstance(item, WebsiteAudit) and new:
        emit(
            session,
            item.company_id,
            "audit:" + item.id,
            "audited",
            {"summary": item.summary, "state": item.state, "audit_id": item.id},
            at=item.created_at,
        )
    elif isinstance(item, Assessment) and new:
        emit(
            session,
            item.company_id,
            "assessment:" + item.id,
            "ai_assessed",
            {"assessment_id": item.id, "score": item.result.get("score"), "origin": item.origin},
            at=item.created_at,
        )
    elif isinstance(item, Draft) and new:
        company = conversation_company(session, item.conversation_id)
        if company:
            emit(
                session,
                company,
                "draft:" + item.id,
                "draft_edited" if item.revision > 1 else "drafted",
                {
                    "draft_id": item.id,
                    "revision": item.revision,
                    "step": item.sequence_step,
                    "channel": "email",
                    "mailbox_id": item.snapshot.get("mailbox_id"),
                },
                actor=item.created_by,
                at=item.created_at,
                url=f"/drafts/{item.id}",
            )
    elif isinstance(item, DraftApproval) and new:
        draft = session.get(Draft, item.draft_id)
        if draft:
            company = conversation_company(session, draft.conversation_id)
            if company:
                emit(
                    session,
                    company,
                    "approval:" + item.draft_id,
                    "approved",
                    {"draft_id": draft.id},
                    actor=item.approved_by,
                    at=item.approved_at,
                    url=f"/drafts/{draft.id}",
                )
    elif isinstance(item, SendIntent):
        capture_intent(session, item)
    elif isinstance(item, InboxMessage) and new and item.conversation_id:
        company = conversation_company(session, item.conversation_id)
        if company:
            kind = {
                "bounce": "bounced",
                "unsubscribe": "unsubscribed",
                "delivery_delayed": "delivery_delay",
                "delivery_notice": "delivery_notice",
                "not_interested": "stopped",
            }.get(item.classification, "replied")
            emit(
                session,
                company,
                "inbox:" + item.id,
                kind,
                {
                    "classification": item.classification,
                    "reason": item.classification,
                    "mailbox_id": item.mailbox_id,
                    "channel": "email",
                },
                at=item.received_at,
                url="/inbox?conversation=" + item.conversation_id,
            )
    elif isinstance(item, CallRecord) and new:
        emit(
            session,
            item.company_id,
            "call:" + item.id,
            "meeting" if item.outcome == "meeting_booked" else "call",
            {
                "outcome": item.outcome,
                "call_id": item.id,
                "notes": item.notes,
                "due_at": item.due_at.isoformat() if item.due_at else "",
                "channel": "call",
            },
            actor=item.actor_id,
            at=item.created_at,
            url=f"/calls#{item.id}",
        )
    elif isinstance(item, PipelineEvent) and new:
        opportunity = session.get(Opportunity, item.opportunity_id)
        company = (
            conversation_company(session, opportunity.conversation_id) if opportunity else None
        )
        if company and opportunity:
            emit(
                session,
                company,
                "pipeline:" + item.id,
                "stage_changed",
                {
                    "stage": item.stage,
                    "reason": item.note,
                    "next_action": opportunity.next_action,
                    "due_at": opportunity.next_due_at.isoformat()
                    if opportunity.next_due_at
                    else "",
                },
                actor=item.actor_id,
                at=item.created_at,
                url="/pipeline",
            )
    elif isinstance(item, DemoVersion):
        emit(
            session,
            item.company_id,
            f"demo:{item.id}:{item.state}",
            "concept_" + item.state,
            {"demo_id": item.id, "version": item.version},
            actor=item.approved_by or item.created_by,
            at=item.approved_at or item.created_at,
            url="/demos",
        )
    elif isinstance(item, AuditEvent) and new:
        capture_audit_event(session, item)


def capture_audit_event(session: Session, item: AuditEvent) -> None:
    """Link legacy audit decisions not represented by a more specific immutable record."""
    company: str | None = None
    if item.action == "company.website_review":
        company = item.entity_id
    elif item.action == "conversation.stopped" and item.entity_id:
        company = conversation_company(session, item.entity_id)
    elif item.action == "draft.rejected" and item.entity_id:
        draft = session.get(Draft, item.entity_id)
        if draft:
            company = conversation_company(session, draft.conversation_id)
    if company:
        kind = {
            "company.website_review": "enriched",
            "conversation.stopped": "stopped",
            "draft.rejected": "draft_rejected",
        }[item.action]
        emit(
            session,
            company,
            "legacy_audit:" + item.id,
            kind,
            {"audit_event_id": item.id, "reason": item.details.get("reason", item.action)},
            actor=item.actor_id,
            at=item.created_at,
        )


def after_flush(session: Session, context: UOWTransaction) -> None:
    """Append events after database IDs exist, atomically before the outer commit."""
    for item in list(session.new):
        capture_record(session, item)
    for item in list(session.dirty):
        if isinstance(item, (SendIntent, DemoVersion)) and session.is_modified(
            item, include_collections=False
        ):
            capture_record(session, item, new=False)


def install_capture() -> None:
    """Register once for application sessions, never for schema migration connections."""
    if not event.contains(Session, "after_flush", after_flush):
        event.listen(Session, "after_flush", after_flush)
