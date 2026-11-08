"""Six authenticated dashboard views and the permitted Phase 1 actions."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.preferences import Preferences, SettingsConflict, save_preferences
from app.jobs.queue import enqueue_health

router = APIRouter()


@router.get("/status/worker", response_class=HTMLResponse)
def worker_status(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Return authenticated worker status for an HTMX polling refresh."""
    return render(request, "worker_status.html", dashboard_context(request, principal, "outbox"))


@router.get("/", response_class=HTMLResponse)
def today(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show the daily workspace overview with explicit preview labeling."""
    return render(request, "today.html", dashboard_context(request, principal, "today"))


@router.get("/leads", response_class=HTMLResponse)
def leads(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show searchable fictional lead cards without enabling research or sending."""
    from app.api.sources import lead_context

    return render(request, "leads.html", lead_context(request, principal))


@router.get("/leads/preview/{lead_id}", response_class=HTMLResponse)
def preview_lead(request: Request, principal: CurrentUser, lead_id: str) -> HTMLResponse:
    """Open a synthetic evidence card without creating a live lead."""
    context = dashboard_context(request, principal, "leads")
    lead = next((item for item in context["leads"] if item["id"] == lead_id), None)
    if lead is None:
        raise HTTPException(status_code=404, detail="Preview lead not found")
    context["lead"] = lead
    return render(request, "lead_detail.html", context)


@router.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show a non-sendable sample queue and real health-job status."""
    from app.api.drafts import outbox_context

    return render(request, "outbox.html", outbox_context(request, principal))


@router.get("/pipeline", response_class=HTMLResponse)
def pipeline(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show the planned reply and call handoff interface as a labeled preview."""
    return render(request, "pipeline.html", dashboard_context(request, principal, "pipeline"))


@router.get("/demos", response_class=HTMLResponse)
def demos(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show future demo types without generating or sending artifacts."""
    return render(request, "demos.html", dashboard_context(request, principal, "demos"))


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show safe persistent preferences and read-only connection status."""
    context = dashboard_context(request, principal, "settings")
    from app.api.playbook import settings_data

    context.update(settings_data(request))
    context["saved"] = request.query_params.get("saved") == "1"
    return render(request, "settings.html", context)


@router.post("/settings", response_class=HTMLResponse, response_model=None)
async def update_settings(request: Request, principal: Owner) -> HTMLResponse | RedirectResponse:
    """Validate and save owner preferences with CSRF and optimistic version checks."""
    fields = await read_form(request)
    check_csrf(principal, fields.pop("csrf_token", ""))
    try:
        version = int(fields.pop("version", ""))
        if version < 0:
            raise ValueError("Invalid settings version")
        fields["preview_enabled"] = fields.get("preview_enabled", "false")
        prefs = Preferences.model_validate(fields)
        save_preferences(services(request).db, prefs, version, principal.user.id, utc_now())
    except SettingsConflict as error:
        context = dashboard_context(request, principal, "settings")
        from app.api.playbook import settings_data

        context.update(settings_data(request))
        context["error"] = str(error)
        return render(request, "settings.html", context, 409)
    except (ValueError, ValidationError):
        context = dashboard_context(request, principal, "settings")
        from app.api.playbook import settings_data

        context.update(settings_data(request))
        context["error"] = (
            "Check the workspace name, timezones and settings version, then try again."
        )
        return render(request, "settings.html", context, 422)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/jobs/health")
async def request_health_job(request: Request, principal: Owner) -> RedirectResponse:
    """Queue a harmless, deduplicated health check; never queue an external action."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    now = utc_now()
    # Duplicate clicks within one minute produce one durable health job.
    enqueue_health(services(request).db, f"health:{now.strftime('%Y%m%d%H%M')}", now)
    return RedirectResponse("/outbox", status_code=303)
