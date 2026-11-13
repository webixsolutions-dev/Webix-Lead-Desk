"""Partner reviews, editable campaign targeting and observed results."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.lead_models import Company
from app.db.partner_models import PartnerDay, PartnerReview
from app.partners.service import review_partner
from app.playbook.catalog import service_names
from app.reporting.service import campaign_results
from app.sending.sender_choice import save_campaign_sender
from app.sending.window import CALGARY
from app.sources.campaigns import DRAFT_ONLY_COUNTRIES, edit_campaign

router = APIRouter()


@router.get("/partners", response_class=HTMLResponse)
def partners_page(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show real campaign outcomes, partner reviews and current daily drafting capacity."""
    context = dashboard_context(request, principal, "partners")
    context["results"] = campaign_results(services(request).db)
    with services(request).db.sessions() as session:
        context["service_names"] = service_names(session)
        context["no_website_count"] = (
            session.scalar(
                select(func.count()).select_from(Company).where(Company.has_website == "false")
            )
            or 0
        )
        day = session.get(PartnerDay, utc_now().astimezone(CALGARY).date().isoformat())
        context["reserved_partners"] = day.reserved if day else 0
        context["partner_reviews"] = list(
            session.execute(
                select(PartnerReview, Company)
                .join(Company)
                .order_by(PartnerReview.reviewed_at.desc())
                .limit(100)
            )
        )
    return render(request, "partners.html", context)


@router.post("/campaigns/{campaign_id}/edit")
async def save_campaign(request: Request, principal: Owner, campaign_id: str) -> RedirectResponse:
    """Save a version-checked campaign edit without rewriting its historical imports."""
    fields = await read_form(request, repeatable=frozenset({"services"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    country = fields.get("country", "")
    try:
        edit_campaign(
            services(request).db,
            campaign_id,
            int(fields.get("version", "0")),
            principal.user.id,
            name=fields.get("name", ""),
            industry=fields.get("industry", ""),
            area=fields.get("area", ""),
            country=country,
            services=[v.strip() for v in fields.get("services", "").split(",") if v.strip()],
            daily_limit=int(fields.get("daily_limit", "0")),
            active=fields.get("active") == "true",
            followup_days=[int(v.strip()) for v in fields["followup_days"].split(",") if v.strip()]
            if "followup_days" in fields
            else None,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    destination = (
        f"/leads/campaigns#campaign-{campaign_id}"
        if country in DRAFT_ONLY_COUNTRIES
        else "/partners"
    )
    return RedirectResponse(destination, 303)


@router.post("/campaigns/{campaign_id}/sender", response_model=None)
async def choose_sender(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Assign a saved mailbox to this campaign without starting email delivery."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        save_campaign_sender(
            services(request).db,
            campaign_id,
            fields.get("mailbox_id", ""),
            int(fields.get("version", "0")),
            principal.user.id,
            from_name=fields.get("from_name"),
        )
    except (SettingsConflict, ValueError) as error:
        from app.api.command import render_campaigns

        return render_campaigns(
            request,
            principal,
            form_context={
                "form_error": str(error),
                "form_error_title": "Campaign sender could not be saved",
                "sender_form_campaign": campaign_id,
                "sender_form": fields,
            },
            status=409 if isinstance(error, SettingsConflict) else 422,
        )
    return RedirectResponse(f"/leads/campaigns#campaign-sender-{campaign_id}", 303)


@router.post("/partners/{company_id}/review")
async def save_partner_review(
    request: Request, principal: Owner, company_id: str
) -> RedirectResponse:
    """Require explicit owner review before partner drafts or delivery."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("reviewed") != "true":
        raise HTTPException(422, "Review country and cross-border rules first")
    try:
        review_partner(
            services(request).db,
            company_id,
            fields.get("country", ""),
            fields.get("source_url", ""),
            fields.get("note", ""),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/leads/" + company_id, 303)
