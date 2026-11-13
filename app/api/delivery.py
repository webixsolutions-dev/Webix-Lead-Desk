"""Owner-only mailbox setup, scheduling and stop actions; HTTP never sends an email."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.vault import store_secret
from app.db.delivery_models import (
    InboxCursor,
    InboxMessage,
    IntegrationSecret,
    MailboxState,
    SendIntent,
)
from app.db.models import AuditEvent, new_id
from app.db.outreach_models import Conversation, Draft, Mailbox
from app.jobs.queue import enqueue
from app.policy.contacts import suppress
from app.sending.providers import PRESETS, connection, readiness_fields
from app.sending.schedule import schedule
from app.sending.signatures import get_signature

router = APIRouter()


def connection_data(request: Request) -> dict[str, Any]:
    """Expose credential presence and health only, never encrypted or plaintext values."""
    from app.sending.diagnostics import diagnostic_context

    tests = diagnostic_context(services(request).db, utc_now())
    with services(request).db.sessions() as session:
        return {
            **tests,
            "mailbox_signatures": {
                mailbox_id: get_signature(session, mailbox_id)
                for mailbox_id in session.scalars(select(Mailbox.id))
            },
            "mailbox_test_request_ids": {
                mailbox_id: new_id() for mailbox_id in session.scalars(select(Mailbox.id))
            },
            "sender_states": {
                item.mailbox_id: item for item in session.scalars(select(MailboxState))
            },
            "inbox_cursors": {
                item.mailbox_id: item for item in session.scalars(select(InboxCursor))
            },
            "connected_keys": set(session.scalars(select(IntegrationSecret.key))),
            "readiness_for": readiness_fields,
            "provider_presets": PRESETS,
            "vault_ready": bool(services(request).settings.encryption_key),
        }


@router.post("/settings/connections")
async def save_connection(request: Request, principal: Owner) -> RedirectResponse:
    """Accept write-only credentials over the authenticated CSRF-protected settings form."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    app = services(request)
    key = fields.get("key", "")
    with app.db.sessions() as session:
        valid_mailbox = key.startswith("mailbox:") and session.get(Mailbox, key[8:]) is not None
    if key not in {"openai", "firecrawl", "places", "pagespeed"} and not valid_mailbox:
        raise HTTPException(422, "Choose an existing connection")
    try:
        store_secret(app.db, app.settings, key, fields.get("credential", ""), principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(
        {
            "openai": "/settings#research-credentials",
            "pagespeed": "/settings#acquisition",
        }.get(key, "/settings#mail-delivery"),
        303,
    )


@router.post("/settings/mailboxes/{mailbox_id}/readiness")
async def mailbox_readiness(
    request: Request, principal: Owner, mailbox_id: str
) -> RedirectResponse:
    """Record owner evidence; enabling a profile does not enable a worker's live-send flag."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    app = services(request)
    try:
        daily, hourly = int(fields.get("daily_limit", "10")), int(fields.get("hourly_limit", "10"))
        if not 1 <= daily <= 3000 or not 1 <= hourly <= 50:
            raise ValueError("Use bounded provider limits; ramp limits still apply")
        evidence = fields.get("evidence", "").strip()
        if not 20 <= len(evidence) <= 1000:
            raise ValueError("Record dated evidence of the sender checks and test acceptance")
        readiness: dict[str, Any] = {}
        readiness.update(evidence=evidence, reviewed_at=utc_now().isoformat())
        enabled = fields.get("enabled") == "true"
        with app.db.sessions.begin() as session:
            mailbox = session.get(Mailbox, mailbox_id)
            if not mailbox or mailbox.provider not in {"titan", "hostinger", "gmail"}:
                raise ValueError("Choose an existing Titan, Hostinger or Gmail sender")
            required = readiness_fields(mailbox)
            readiness.update({key: fields.get(key) == "true" for key in required})
            caps = PRESETS[mailbox.provider]
            if hourly > caps["hourly"] or daily > (
                3000 if mailbox.provider == "hostinger" else caps["daily"]
            ):
                raise ValueError("Limits exceed this provider profile's conservative ceiling")
            if enabled and (
                not all(readiness[key] for key in required)
                or not app.settings.encryption_key
                or not session.get(IntegrationSecret, "mailbox:" + mailbox_id)
            ):
                raise ValueError(
                    "Complete every readiness check and save the mailbox credential first"
                )
            state = session.get(MailboxState, mailbox_id)
            if state is None:
                state = MailboxState(mailbox_id=mailbox_id, updated_by=principal.user.id)
                session.add(state)
            state.readiness, state.daily_limit, state.hourly_limit = readiness, daily, hourly
            state.updated_by = principal.user.id
            if enabled and state.ramp_started_at is None:
                state.ramp_started_at = utc_now()
            changed_connection = connection(
                mailbox.provider,
                {
                    key: fields[key]
                    for key in ("smtp_host", "smtp_port", "imap_host", "imap_port")
                    if key in fields
                },
            )
            mailbox.connection = changed_connection
            mailbox.enabled = enabled
            if not enabled:
                session.execute(
                    update(SendIntent)
                    .where(
                        SendIntent.mailbox_id == mailbox_id,
                        SendIntent.state.in_(["scheduled", "held"]),
                    )
                    .values(state="cancelled", hold_reason="owner_paused_mailbox")
                )
                session.execute(
                    update(Draft)
                    .where(
                        Draft.conversation_id.in_(
                            select(Conversation.id).where(Conversation.mailbox_id == mailbox_id)
                        ),
                        Draft.state.in_(["draft", "approved"]),
                    )
                    .values(state="cancelled")
                )
            session.add(
                AuditEvent(
                    actor_id=principal.user.id, action="mailbox.readiness", entity_id=mailbox_id
                )
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings#mail-delivery", 303)


@router.post("/drafts/{draft_id}/schedule")
async def queue_send(request: Request, principal: Owner, draft_id: str) -> RedirectResponse:
    """Schedule one already-approved exact revision for the PC/VPS worker."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        schedule(services(request).db, draft_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/outbox", 303)


@router.post("/delivery/{intent_id}/reconcile")
async def reconcile_send(request: Request, principal: Owner, intent_id: str) -> RedirectResponse:
    """Queue a read-only Sent lookup; never offer a resend-unknown button."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions() as session:
        item = session.get(SendIntent, intent_id)
        if not item or item.state != "uncertain":
            raise HTTPException(422, "Choose an uncertain transmission")
    now = utc_now()
    enqueue(
        services(request).db,
        f"reconcile:{intent_id}:{int(now.timestamp()) // 60}",
        "reconcile",
        {"intent_id": intent_id},
        now,
    )
    return RedirectResponse("/outbox", 303)


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Render bounded escaped plain-text replies without remote content or executable mail."""
    context = dashboard_context(request, principal, "outbox")
    with services(request).db.sessions() as session:
        context["messages"] = list(
            session.scalars(
                select(InboxMessage).order_by(InboxMessage.received_at.desc()).limit(100)
            )
        )
        context["reply_companies"] = {
            row.id: row.company_id for row in session.scalars(select(Conversation))
        }
    return render(request, "inbox.html", context)


@router.post("/conversations/{conversation_id}/stop")
async def stop_conversation(
    request: Request, principal: Owner, conversation_id: str
) -> RedirectResponse:
    """Stop a conversation and optionally suppress the address after human review."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions.begin() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found")
        conversation.state = "stopped"
        drafts = list(
            session.scalars(select(Draft).where(Draft.conversation_id == conversation_id))
        )
        recipient = drafts[-1].snapshot["recipient"] if drafts else ""
        session.execute(
            update(SendIntent)
            .where(
                SendIntent.conversation_id == conversation_id,
                SendIntent.state.in_(["scheduled", "held"]),
            )
            .values(state="cancelled", hold_reason="owner_stop")
        )
        session.execute(
            update(Draft)
            .where(Draft.conversation_id == conversation_id, Draft.state.in_(["draft", "approved"]))
            .values(state="cancelled")
        )
        session.add(
            AuditEvent(
                actor_id=principal.user.id, action="conversation.stopped", entity_id=conversation_id
            )
        )
    if fields.get("suppress") == "true" and recipient:
        suppress(services(request).db, recipient, "Owner-confirmed recipient stop")
    return RedirectResponse("/inbox", 303)


@router.post("/conversations/{conversation_id}/review-delay")
async def review_delivery_delay(
    request: Request, principal: Owner, conversation_id: str
) -> RedirectResponse:
    """Clear only a reviewed delay hold; existing sends still require manual rescheduling."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    note = fields.get("note", "").strip()
    if fields.get("reviewed") != "true" or not 10 <= len(note) <= 500:
        raise HTTPException(422, "Review delivery status in the sender mailbox and record evidence")
    with services(request).db.sessions.begin() as session:
        conversation = session.get(Conversation, conversation_id)
        messages = list(
            session.scalars(
                select(InboxMessage).where(InboxMessage.conversation_id == conversation_id)
            )
        )
        if (
            not conversation
            or conversation.state != "held"
            or not messages
            or not any(m.classification == "delivery_delayed" for m in messages)
            or any(
                m.classification not in {"delivery_delayed", "delivery_notice"} for m in messages
            )
        ):
            raise HTTPException(409, "This hold includes a reply or another review requirement")
        conversation.state = "active" if conversation.first_message_id else "draft"
        session.add(
            AuditEvent(
                actor_id=principal.user.id,
                action="delivery.delay_reviewed",
                entity_id=conversation_id,
                details={"note": note},
            )
        )
    return RedirectResponse("/outbox", 303)
