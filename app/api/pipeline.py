"""Team sales review and call handoffs, separate from owner-only sending permissions."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.db.delivery_models import InboxMessage
from app.db.outreach_models import Conversation
from app.db.pipeline_models import Opportunity, PipelineEvent
from app.pipeline.service import TRANSITIONS, board, capture_reply, update_opportunity
from app.sending.window import CALGARY

router = APIRouter()


def cents(value: str) -> int:
    """Parse an exact non-negative CAD amount, rejecting fractional cents and non-finite values."""
    try:
        amount = Decimal(value or "0")
        if (
            not amount.is_finite()
            or amount < 0
            or amount > 1_000_000
            or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise ValueError("Use a non-negative CAD amount with at most two decimals")
        return int(amount * 100)
    except InvalidOperation as error:
        raise ValueError("Use a valid CAD amount") from error


@router.get("/pipeline/{card_id}", response_class=HTMLResponse)
def opportunity_page(request: Request, principal: CurrentUser, card_id: str) -> HTMLResponse:
    """Show a real business handoff, its reply context and an editable next action."""
    context = dashboard_context(request, principal, "pipeline")
    data = board(services(request).db)
    selected = next((row for row in data["opportunities"] if row[0].id == card_id), None)
    if selected is None:
        raise HTTPException(404, "Opportunity not found")
    card, conversation, company, contact = selected
    context.update(
        data,
        card=card,
        conversation=conversation,
        company_record=company,
        contact=contact,
        next_stages=[card.stage, *sorted(TRANSITIONS.get(card.stage, set()))],
        meeting_local=card.meeting_at.astimezone(CALGARY).strftime("%Y-%m-%dT%H:%M")
        if card.meeting_at
        else "",
    )
    with services(request).db.sessions() as session:
        context["history"] = list(
            session.scalars(
                select(PipelineEvent)
                .where(PipelineEvent.opportunity_id == card.id)
                .order_by(PipelineEvent.created_at.desc())
                .limit(30)
            )
        )
        context["replies"] = list(
            session.scalars(
                select(InboxMessage)
                .where(InboxMessage.conversation_id == conversation.id)
                .order_by(InboxMessage.received_at.desc())
                .limit(10)
            )
        )
    return render(request, "opportunity.html", context)


@router.post("/pipeline/{card_id}")
async def save_opportunity(
    request: Request, principal: CurrentUser, card_id: str
) -> RedirectResponse:
    """Save the exact reviewed stage, owner, meeting and commercial values."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        meeting = (
            datetime.fromisoformat(fields["meeting_at"]).replace(tzinfo=CALGARY)
            if fields.get("meeting_at")
            else None
        )
        update_opportunity(
            services(request).db,
            card_id,
            int(fields.get("version", "0")),
            principal.user.id,
            stage=fields.get("stage", ""),
            assigned_to=fields.get("assigned_to") or None,
            summary=fields.get("summary", ""),
            next_action=fields.get("next_action", ""),
            meeting_at=meeting,
            meeting_url=fields.get("meeting_url", ""),
            setup_cad_cents=cents(fields.get("setup_cad", "0")),
            recurring_cad_cents=cents(fields.get("recurring_cad", "0")),
            outcome_note=fields.get("outcome_note", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/pipeline/{card_id}", 303)


@router.post("/pipeline/sync/replies")
async def import_existing_replies(request: Request, principal: CurrentUser) -> RedirectResponse:
    """Backfill review cards for replies captured before this feature, without reprocessing mail."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions.begin() as session:
        rows = session.execute(
            select(InboxMessage, Conversation)
            .join(Conversation, Conversation.id == InboxMessage.conversation_id)
            .order_by(InboxMessage.received_at)
            .limit(1000)
        )
        for message, conversation in rows:
            if not session.scalar(
                select(Opportunity.id).where(Opportunity.conversation_id == conversation.id)
            ):
                capture_reply(session, conversation, message.classification, message.received_at)
    return RedirectResponse("/pipeline", 303)
