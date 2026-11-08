"""Private pilot readiness and explicit owner launch reviews."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.pilot.readiness import readiness
from app.pilot.service import cohort, remove_member, save_member, transition

router = APIRouter()


@router.get("/pilot", response_class=HTMLResponse)
def pilot_page(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show real cohort progress and explicit provider/configuration gaps."""
    app = services(request)
    context = dashboard_context(request, principal, "today")
    context.update(cohort(app.db))
    context["readiness"] = readiness(app.db, app.settings)
    return render(request, "pilot.html", context)


@router.post("/pilot/members")
async def review_member(request: Request, principal: Owner) -> RedirectResponse:
    """Record specific owner research review for one imported pilot candidate."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        save_member(
            services(request).db,
            fields.get("company_id", ""),
            fields.get("sector", ""),
            fields.get("note", ""),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/pilot", 303)


@router.post("/pilot/review")
async def review_pilot(request: Request, principal: Owner) -> RedirectResponse:
    """Activate, pause or complete a cohort without approving any outgoing message."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("confirmed") != "true":
        raise HTTPException(422, "Confirm your pilot review")
    try:
        transition(
            services(request).db,
            fields.get("target", ""),
            int(fields.get("version", "0")),
            fields.get("note", ""),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/pilot", 303)


@router.post("/pilot/members/{company_id}/remove")
async def remove_candidate(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Remove a draft cohort choice, never its underlying lead or evidence."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        remove_member(services(request).db, company_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/pilot", 303)
