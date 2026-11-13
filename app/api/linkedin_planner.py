"""Owner quota controls and a read-only member-scoped daily plan."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.delivery_models import IntegrationSecret
from app.db.lead_models import Campaign, CampaignLead
from app.db.linkedin_models import LinkedInProfile
from app.db.models import Job, User, WorkerInstance, WorkspaceSetting
from app.jobs.queue import enqueue
from app.linkedin.board import planned_tasks
from app.linkedin.campaigns import PREFIX, active_campaign, save_routing
from app.linkedin.planner import plan_day
from app.linkedin.quotas import Quotas, save_quotas
from app.sources.campaigns import archived_campaign_ids
from app.sources.enrichment.config import get_config
from app.sources.importing import import_candidates, parse_csv

router = APIRouter()


@router.get("/linkedin/campaigns", response_class=HTMLResponse)
def campaigns(request: Request, principal: Owner) -> HTMLResponse:
    """Configure LinkedIn by industry and market."""
    return _campaigns_page(request, principal)


def _campaigns_page(
    request: Request,
    principal: Owner,
    *,
    error: str = "",
    status: int = 200,
    error_title: str = "LinkedIn campaign could not be saved",
) -> HTMLResponse:
    context = dashboard_context(request, principal, "linkedin")
    with services(request).db.sessions() as session:
        context["linkedin_campaigns"] = list(
            session.execute(
                select(Campaign, func.count(CampaignLead.id))
                .outerjoin(CampaignLead, CampaignLead.campaign_id == Campaign.id)
                .where(Campaign.id.not_in(archived_campaign_ids(session)))
                .group_by(Campaign.id)
                .order_by(Campaign.area, Campaign.name)
            )
        )
        context["profiles"] = list(
            session.scalars(
                select(LinkedInProfile)
                .join(User, LinkedInProfile.user_id == User.id)
                .where(LinkedInProfile.status == "active", User.active.is_(True))
                .order_by(LinkedInProfile.display_name)
            )
        )
        context["routes"] = {
            item.key.removeprefix(PREFIX): item
            for item in session.scalars(
                select(WorkspaceSetting).where(WorkspaceSetting.key.startswith(PREFIX))
            )
        }
        recent_searches: dict[str, Job] = {}
        for job in session.scalars(
            select(Job)
            .where(Job.kind == "linkedin_discover")
            .order_by(Job.created_at.desc())
            .limit(100)
        ):
            recent_searches.setdefault(job.payload["campaign_id"], job)
        context["recent_searches"] = recent_searches
    return render(
        request,
        "linkedin_campaigns.html",
        {
            **context,
            "form_error": error,
            "form_error_title": error_title,
        },
        status,
    )


@router.post("/linkedin/campaigns/{campaign_id}", response_model=None)
async def update_campaign(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Start or pause LinkedIn planning for the chosen team member."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    app = services(request)
    try:
        save_routing(
            app.db,
            campaign_id,
            fields.get("profile_id", ""),
            fields.get("active") == "true",
            int(fields.get("version", "0")),
            principal.user.id,
        )
    except (ValueError, SettingsConflict) as error:
        return _campaigns_page(
            request,
            principal,
            error=str(error),
            status=409 if isinstance(error, SettingsConflict) else 422,
        )
    return RedirectResponse(f"/linkedin/campaigns#linkedin-campaign-{campaign_id}", 303)


@router.post("/linkedin/campaigns/{campaign_id}/import", response_model=None)
async def import_businesses(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Bring existing leads into an active LinkedIn campaign without email jobs."""
    fields = await read_form(request, maximum=1_100_000)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        rows = parse_csv(fields.get("csv_text", "").encode())
        import_candidates(
            services(request).db, campaign_id, rows, "csv", principal.user.id, linkedin=True
        )
    except (ValueError, IntegrityError) as error:
        return _campaigns_page(
            request,
            principal,
            error=str(error)
            if isinstance(error, ValueError)
            else "Import changed in another tab. Retry the CSV.",
            error_title="LinkedIn businesses could not be imported",
            status=422 if isinstance(error, ValueError) else 409,
        )
    return RedirectResponse(f"/linkedin/campaigns?imported=1#linkedin-campaign-{campaign_id}", 303)


@router.post("/linkedin/campaigns/{campaign_id}/discover", response_model=None)
async def discover_campaign(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Queue one budgeted discovery batch for LinkedIn."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    db = services(request).db
    now = utc_now()
    config, _ = get_config(db)
    with db.sessions() as session:
        rule = active_campaign(session, campaign_id)
        version = rule.version if rule else None
        worker = session.get(WorkerInstance, "primary")
        runtime = (worker.runtime_config or {}) if worker else {}
        supported = (
            worker and worker.lease_expires_at > now and runtime.get("linkedin_campaigns") == 1
        )
        key_saved = session.get(IntegrationSecret, "openai") is not None
    if version is None or not supported:
        error = (
            "Activate this LinkedIn campaign first."
            if version is None
            else "Start worker v0.33.1 or newer before searching for businesses."
        )
        return _campaigns_page(request, principal, error=error, status=422)
    if config.provider == "openai_web_search" and not (
        runtime.get("openai_search") and (key_saved or runtime.get("openai_env_key"))
    ):
        return _campaigns_page(
            request,
            principal,
            error=(
                "Start a worker with OpenAI search (Windows v0.33.2 or newer) "
                "and save the key in Discovery & AI."
            ),
            status=422,
        )
    enqueue(
        db,
        f"linkedin:discover:{campaign_id}:{version}:{now:%Y%m%d%H%M}",
        "linkedin_discover",
        {
            "campaign_id": campaign_id,
            "actor_id": principal.user.id,
            "routing_version": version,
            "remaining_batches": 1,
        },
        now,
    )
    return RedirectResponse(
        f"/linkedin/campaigns?search=queued#linkedin-campaign-{campaign_id}", 303
    )


@router.get("/linkedin/plan", response_class=HTMLResponse)
def daily_plan(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Review the member's persistent plan without exposing an unapproved action."""
    context = dashboard_context(request, principal, "linkedin")
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
