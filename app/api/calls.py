"""Reviewer-accessible human call list and attributable outcomes."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.calls.board import call_history, call_list
from app.calls.service import OUTCOMES, record_call
from app.core.clock import utc_now
from app.core.timezones import zone
from app.db.acquisition_models import CallRecord
from app.jobs.queue import enqueue

router = APIRouter()


@router.get("/calls", response_class=HTMLResponse)
def calls_page(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Display a mobile and print-ready call list without opening Settings."""
    context = dashboard_context(request, principal, "today")
    context.update(
        call_cards=call_list(services(request).db),
        call_history=call_history(services(request).db),
        outcomes=OUTCOMES,
        ai_enabled=services(request).settings.openai_enabled,
    )
    return render(request, "calls.html", context)


@router.post("/calls/{company_id}/outcome")
async def outcome(request: Request, principal: CurrentUser, company_id: str) -> RedirectResponse:
    """Record a completed human call with CSRF protection and double-submit deduplication."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        due = (
            datetime.fromisoformat(fields["due_at"]).replace(tzinfo=zone("America/Edmonton"))
            if fields.get("due_at")
            else None
        )
        record_call(
            services(request).db,
            company_id,
            fields.get("campaign_id", ""),
            principal.user.id,
            fields.get("request_key", ""),
            fields.get("outcome", ""),
            fields.get("notes", ""),
            due_at=due,
            email=fields.get("email", ""),
            consent=fields.get("consent") == "true",
            consent_note=fields.get("consent_note", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/calls#history", 303)


@router.post("/calls/{call_id}/notes")
async def notes(request: Request, principal: CurrentUser, call_id: str) -> RedirectResponse:
    """Queue optional model suggestions; only the owner can approve resulting email."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions() as session:
        if not session.get(CallRecord, call_id):
            raise HTTPException(404, "Call not found")
    enqueue(
        services(request).db, "call_notes:" + call_id, "call_notes", {"call_id": call_id}, utc_now()
    )
    return RedirectResponse("/calls#" + call_id, 303)
