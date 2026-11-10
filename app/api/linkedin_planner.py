"""Owner quota controls and a read-only member-scoped daily plan."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.linkedin.board import planned_tasks
from app.linkedin.planner import plan_day
from app.linkedin.quotas import Quotas, save_quotas

router = APIRouter()


@router.get("/linkedin/plan", response_class=HTMLResponse)
def daily_plan(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Review the member's persistent plan without exposing an unapproved action."""
    context = dashboard_context(request, principal, "today")
    context["planned_tasks"] = planned_tasks(services(request).db, principal.user, utc_now())
    if request.query_params.get("profile"):
        context["planned_tasks"] = [
            row
            for row in context["planned_tasks"]
            if row["profile"].id == request.query_params["profile"]
        ]
    return render(request, "linkedin_plan.html", context)


@router.post("/linkedin/plan")
async def plan_now(request: Request, principal: Owner) -> RedirectResponse:
    """Prepare remaining weekday work without overriding windows or capacities."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    plan_day(services(request).db, utc_now())
    return RedirectResponse("/linkedin/plan", 303)


@router.post("/settings/linkedin/{profile_id}/quotas")
async def update_quotas(request: Request, principal: Owner, profile_id: str) -> RedirectResponse:
    """Validate all requested profile limits against immutable upper ceilings."""
    fields = await read_form(request)
    check_csrf(principal, fields.pop("csrf_token", ""))
    try:
        save_quotas(
            services(request).db, profile_id, Quotas.model_validate(fields), principal.user.id
        )
    except ValueError as error:
        raise HTTPException(422, "Choose limits within the displayed ceilings") from error
    return RedirectResponse("/settings/linkedin", 303)
