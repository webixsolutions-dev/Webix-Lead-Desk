"""Canonical work areas and in-place owner review, using existing approval endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.delivery_models import InboxMessage
from app.db.lead_models import Contact
from app.db.linkedin_models import Person, Touch
from app.db.models import AuditEvent, WorkspaceSetting
from app.db.outreach_models import Conversation
from app.drafting.review import review_brief
from app.reporting.activity import activity
from app.reporting.conversations import thread, threads

router = APIRouter()


@router.get("/command/activity", response_class=HTMLResponse)
def recent_activity(request: Request, principal: Owner) -> HTMLResponse:
    """Poll persisted activity only; no provider calls, mutations or full queue refresh."""
    return render(request, "command_activity.html", activity(services(request).db, utc_now()))


@router.get("/conversations", response_class=HTMLResponse)
def conversations(request: Request, principal: Owner) -> HTMLResponse:
    """Show one recipient per card, optionally filtered to work needing review."""
    context = dashboard_context(request, principal, "conversations")
    tab = request.query_params.get("tab", "all")
    if tab not in {"all", "drafts", "replies", "scheduled"}:
        raise HTTPException(422, "Unknown conversation filter")
    rows = threads(services(request).db, utc_now())
    context.update(threads=[r for r in rows if tab == "all" or r[tab]], conversation_tab=tab)
    return render(request, "conversations.html", context)


@router.get("/conversations/contact/{contact_id}", response_class=HTMLResponse)
@router.get("/conversations/people/{person_id}", response_class=HTMLResponse)
def person_thread(
    request: Request, principal: Owner, contact_id: str = "", person_id: str = ""
) -> HTMLResponse:
    """Open confirmed recipient history without merging by company or similar names."""
    context = dashboard_context(request, principal, "conversations")
    try:
        context.update(thread(services(request).db, contact_id=contact_id, person_id=person_id))
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    return render(request, "conversation_thread.html", context)


@router.get("/command/replies/{channel}/{message_id}", response_class=HTMLResponse)
def reply_review(request: Request, principal: Owner, channel: str, message_id: str) -> HTMLResponse:
    """Present the actual inbound sender before offering an exact-recipient reply editor."""
    context = dashboard_context(request, principal, "conversations")
    db = services(request).db
    with db.sessions() as session:
        if channel == "email":
            message = session.get(InboxMessage, message_id)
            if not message:
                raise HTTPException(404, "Message not found")
            conversation = (
                session.get(Conversation, message.conversation_id)
                if message.conversation_id
                else None
            )
            contact = session.get(Contact, conversation.contact_id) if conversation else None
            matched = bool(
                contact
                and contact.email == message.sender
                and conversation
                and conversation.mailbox_id == message.mailbox_id
            )
            company_id = conversation.company_id if matched and conversation else None
            context.update(
                reply_sender=message.sender,
                reply_text=message.body,
                reply_at=message.received_at,
                reply_url=f"/leads/{company_id}/draft?kind=reply" if matched else "",
            )
        elif channel == "linkedin":
            touch = session.get(Touch, message_id)
            if not touch or touch.direction != "inbound" or touch.channel != "linkedin":
                raise HTTPException(404, "Message not found")
            person = session.get(Person, touch.person_id) if touch.person_id else None
            company_id = person.company_id if person else None
            context.update(
                reply_sender=person.full_name if person else "Unmatched person",
                reply_text=touch.text,
                reply_at=touch.occurred_at,
                reply_url="/linkedin/conversations/" + person.id if person else "",
            )
        else:
            raise HTTPException(404, "Channel not found")
        context["review"] = review_brief(db, company_id) if company_id else None
    context.update(reply_channel=channel, reply_id=message_id)
    return render(request, "command_reply.html", context)


@router.post("/command/replies/{channel}/{message_id}/reviewed")
async def acknowledge_reply(
    request: Request, principal: Owner, channel: str, message_id: str
) -> RedirectResponse:
    """Record an explicit reason; acknowledgement does not reply or stop a conversation."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    reason = fields.get("reason", "").strip()
    if not 5 <= len(reason) <= 500:
        raise HTTPException(422, "Record a review reason of 5–500 characters")
    with services(request).db.sessions.begin() as session:
        valid = session.get(InboxMessage, message_id) if channel == "email" else None
        if channel == "linkedin":
            touch = session.get(Touch, message_id)
            if touch and touch.channel == "linkedin" and touch.direction == "inbound":
                valid = touch  # type: ignore[assignment]
        if not valid:
            raise HTTPException(404, "Message not found")
        action = f"queue.{channel}.reviewed"
        if not session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == action, AuditEvent.entity_id == message_id
            )
        ):
            session.add(
                AuditEvent(
                    actor_id=principal.user.id,
                    action=action,
                    entity_id=message_id,
                    details={"reason": reason},
                )
            )
    return RedirectResponse("/", 303)


@router.get("/pipeline/bookings", response_class=HTMLResponse)
def bookings(request: Request, principal: Owner) -> HTMLResponse:
    """Keep bookings within Pipeline, retaining the existing guarded actions."""
    from app.api.bookings import board

    return board(request, principal)


@router.get("/pipeline/demos", response_class=HTMLResponse)
def demos(request: Request, principal: Owner) -> HTMLResponse:
    """Keep reviewed concepts within Pipeline."""
    from app.api.dashboard import demos as page

    return page(request, principal)


@router.get("/leads/calls", response_class=HTMLResponse)
def calls(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Preserve staff access to attributable human calling work."""
    from app.api.calls import calls_page

    return calls_page(request, principal)


@router.get("/leads/results", response_class=HTMLResponse)
def results(request: Request, principal: Owner) -> HTMLResponse:
    """Display recorded campaign results beneath Leads."""
    from app.api.partners import partners_page

    return partners_page(request, principal)


@router.get("/leads/campaigns", response_class=HTMLResponse)
def campaigns(request: Request, principal: Owner) -> HTMLResponse:
    """Centralize creation and edits of reusable targeting without changing send windows."""
    return render_campaigns(request, principal)


def render_campaigns(
    request: Request,
    principal: Owner,
    *,
    form_context: dict[str, Any] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Keep campaign settings available when a sender save needs correction."""
    from app.api.sources import lead_context
    from app.db.outreach_models import Mailbox
    from app.reporting.service import campaign_results
    from app.sending.campaign_sender import SENDER_KEY_PREFIX, selectable_sender
    from app.sending.signatures import CAMPAIGN_SIGNATURE_PREFIX
    from app.sources.campaigns import CAMPAIGN_COUNTRIES, DRAFT_ONLY_COUNTRIES

    context = lead_context(request, principal)
    context["results"] = campaign_results(services(request).db)
    context["campaign_countries"] = CAMPAIGN_COUNTRIES
    context["draft_only_countries"] = DRAFT_ONLY_COUNTRIES
    with services(request).db.sessions() as session:
        assignments = {
            item.key.removeprefix(SENDER_KEY_PREFIX): item
            for item in session.scalars(
                select(WorkspaceSetting).where(WorkspaceSetting.key.startswith(SENDER_KEY_PREFIX))
            )
        }
        context["campaign_sender_requirements"] = {
            key: item.value for key, item in assignments.items()
        }
        context["campaign_sender_versions"] = {
            key: item.version for key, item in assignments.items()
        }
        mailboxes = list(session.scalars(select(Mailbox).order_by(Mailbox.email)))
        context["mailbox_names"] = {mailbox.email: mailbox.sender_name for mailbox in mailboxes}
        context["campaign_sender_choices"] = {
            row["campaign"].id: [
                mailbox
                for mailbox in mailboxes
                if selectable_sender(
                    row["campaign"],
                    mailbox,
                    context["campaign_sender_requirements"].get(row["campaign"].id, {}),
                    for_assignment=True,
                )
            ]
            for row in context["results"]
        }
        context["campaign_signatures"] = {
            item.key.removeprefix(CAMPAIGN_SIGNATURE_PREFIX): item
            for item in session.scalars(
                select(WorkspaceSetting).where(
                    WorkspaceSetting.key.startswith(CAMPAIGN_SIGNATURE_PREFIX)
                )
            )
        }
    context.update(form_context or {})
    return render(request, "campaigns.html", context, status)


@router.get("/conversations/drafts", response_class=HTMLResponse)
def draft_queue(request: Request, principal: Owner) -> HTMLResponse:
    """Retain exact batch approval and send records in a canonical conversation view."""
    from app.api.dashboard import outbox

    return outbox(request, principal)


LEGACY = {
    "/outbox": "/conversations/drafts",
    "/inbox": "/conversations/inbox",
    "/bookings": "/pipeline/bookings",
    "/demos": "/pipeline/demos",
    "/calls": "/leads/calls",
    "/partners": "/leads/results",
}


@router.get("/outbox")
@router.get("/inbox")
@router.get("/bookings")
@router.get("/demos")
@router.get("/calls")
@router.get("/partners")
def legacy(request: Request, principal: CurrentUser) -> RedirectResponse:
    """Preserve bookmarks and POST redirects while exposing one canonical navigation."""
    target = LEGACY[request.url.path]
    if request.url.query:
        target += ("&" if "?" in target else "?") + request.url.query
    return RedirectResponse(target, 307)


@router.get("/conversations/inbox", response_class=HTMLResponse)
def inbox_records(request: Request, principal: Owner) -> HTMLResponse:
    """Keep incoming records and stop controls reachable beneath Conversations."""
    from app.api.delivery import inbox

    return inbox(request, principal)
