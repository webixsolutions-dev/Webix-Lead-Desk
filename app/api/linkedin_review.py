"""Attributable owner controls for manual action recovery and sales handoff."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.calls.access import assign_calls
from app.core.clock import utc_now
from app.core.timezones import zone
from app.linkedin.actions import resolve
from app.linkedin.pipeline import update_card
from app.linkedin.review import reply_task, review_sequence, save_voice

router = APIRouter()


@router.post("/linkedin/tasks/{task_id}/resolve")
async def reconcile(request: Request, principal: Owner, task_id: str) -> RedirectResponse:
    """Resolve an action only from a human check of LinkedIn's actual state."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        resolve(
            services(request).db,
            task_id,
            principal.user.id,
            fields.get("outcome", ""),
            fields.get("note", ""),
            utc_now(),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/tasks/" + task_id, 303)


@router.post("/people/{person_id}/sequence")
async def sequence(request: Request, principal: Owner, person_id: str) -> RedirectResponse:
    """Require a new review before overdue or cooled-down work can resume."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        review_sequence(
            services(request).db,
            person_id,
            principal.user.id,
            fields.get("decision", ""),
            fields.get("note", ""),
            utc_now(),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/queue", 303)


@router.post("/people/{person_id}/prepare-reply")
async def prepare_reply(request: Request, principal: Owner, person_id: str) -> RedirectResponse:
    """Prepare a separate response without restarting any cold sequence."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        task = reply_task(services(request).db, person_id, principal.user.id, utc_now())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/tasks/" + task.id, 303)


@router.post("/people/{person_id}/pipeline")
async def pipeline(request: Request, principal: Owner, person_id: str) -> RedirectResponse:
    """Update one reviewed person opportunity without changing any outreach approval."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        due = (
            datetime.fromisoformat(fields["next_due"]).replace(tzinfo=zone("America/Edmonton"))
            if fields.get("next_due")
            else None
        )
        update_card(
            services(request).db,
            person_id,
            principal.user.id,
            fields.get("stage", ""),
            fields.get("note", ""),
            int(fields.get("version", "-1")),
            due,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/pipeline", 303)


@router.post("/settings/linkedin/{profile_id}/voice")
async def voice(request: Request, principal: Owner, profile_id: str) -> RedirectResponse:
    """Owner approves the member's own voice guidance and factual stories."""
    fields = await read_form(request, maximum=20000)
    check_csrf(principal, fields.pop("csrf_token", ""))
    try:
        save_voice(services(request).db, profile_id, principal.user.id, fields)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings/linkedin", 303)


@router.post("/calls/{company_id}/assign")
async def assign(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Assign a business to one member's call list without bypassing routing locks."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        assign_calls(
            services(request).db, company_id, fields.get("profile_id", ""), principal.user.id
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/calls", 303)
