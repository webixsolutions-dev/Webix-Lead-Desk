"""Weekly acquisition evidence, manually observed post totals and owner health reviews."""

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.models import new_id
from app.linkedin.metric_review import PostCounts, record_post, record_profile, resume
from app.linkedin.metrics import report
from app.linkedin.timing import week_start
from app.sending.window import CALGARY

router = APIRouter()


@router.get("/linkedin/results", response_class=HTMLResponse)
def results(request: Request, principal: CurrentUser, week: str = "") -> HTMLResponse:
    """Show actual cohort denominators and only the signed-in member's own records."""
    try:
        data = report(
            services(request).db,
            principal.user.id,
            date.fromisoformat(week or week_start(utc_now())),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    context = dashboard_context(
        request, principal, "outbox" if principal.user.role == "owner" else "linkedin"
    )
    context.update(
        data,
        week_last=data["end"] - timedelta(days=1),
        observation_key=new_id(),
        calgary=CALGARY,
        count_fields=list(PostCounts.model_fields),
    )
    return render(request, "linkedin_results.html", context)


@router.post("/linkedin/results/profiles/{profile_id}")
async def profile_result(
    request: Request, principal: CurrentUser, profile_id: str
) -> RedirectResponse:
    """Record the member's observed SSI and any actual restriction notice."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        record_profile(
            services(request).db,
            profile_id,
            principal.user.id,
            fields.get("key", ""),
            int(fields["ssi"]) if fields.get("ssi") else None,
            fields.get("warning", ""),
            fields.get("restricted") == "true",
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/results", 303)


@router.post("/linkedin/results/posts/{post_id}")
async def post_result(request: Request, principal: CurrentUser, post_id: str) -> RedirectResponse:
    """Append a cumulative post snapshot instead of adding observations together."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        counts = PostCounts.model_validate(
            {name: int(fields.get(name, "0")) for name in PostCounts.model_fields}
        )
        record_post(
            services(request).db,
            post_id,
            principal.user.id,
            fields.get("key", ""),
            counts,
            fields.get("note", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/results", 303)


@router.post("/linkedin/results/profiles/{profile_id}/resume")
async def resume_profile(request: Request, principal: Owner, profile_id: str) -> RedirectResponse:
    """Resume only after an attributable owner review; historical results remain visible."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("reviewed") != "true":
        raise HTTPException(422, "Review targeting and any actual platform restriction first")
    try:
        resume(
            services(request).db, profile_id, principal.user.id, fields.get("note", ""), utc_now()
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/results", 303)
