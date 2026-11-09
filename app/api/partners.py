"""Partner reviews, editable campaign targeting and observed results."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.lead_models import Company
from app.db.partner_models import PartnerDay, PartnerReview
from app.partners.service import review_partner
from app.reporting.service import campaign_results
from app.sending.window import CALGARY
from app.sources.campaigns import edit_campaign

router = APIRouter()


@router.get("/partners", response_class=HTMLResponse)
def partners_page(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show real campaign outcomes, partner reviews and current daily drafting capacity."""
    context = dashboard_context(request, principal, "partners")
    context["results"] = campaign_results(services(request).db)
    with services(request).db.sessions() as session:
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
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        edit_campaign(
            services(request).db,
            campaign_id,
            int(fields.get("version", "0")),
            principal.user.id,
            name=fields.get("name", ""),
            industry=fields.get("industry", ""),
            area=fields.get("area", ""),
            country=fields.get("country", ""),
            services=[v.strip() for v in fields.get("services", "").split(",") if v.strip()],
            daily_limit=int(fields.get("daily_limit", "0")),
            active=fields.get("active") == "true",
            followup_days=[int(v.strip()) for v in fields["followup_days"].split(",") if v.strip()]
            if "followup_days" in fields
            else None,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/partners", 303)


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
