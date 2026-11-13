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
from app.sources.campaigns import (
    DRAFT_ONLY_COUNTRIES,
    archive_campaign,
    campaign_services,
    edit_campaign,
)

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


@router.post("/campaigns/{campaign_id}/edit", response_model=None)
async def save_campaign(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Save a version-checked campaign edit without rewriting its historical imports."""
    fields: dict[str, str] = {}
    try:
        fields = await read_form(request, repeatable=frozenset({"services"}), max_fields=128)
        check_csrf(principal, fields.get("csrf_token", ""))
        country = fields.get("country", "")
        try:
            daily_limit = int(fields.get("daily_limit", "0"))
        except ValueError as error:
            raise ValueError("Enter the daily email cap as a whole number.") from error
        try:
            followup_days = (
                [int(day.strip()) for day in fields["followup_days"].split(",") if day.strip()]
                if "followup_days" in fields
                else None
            )
        except ValueError as error:
            raise ValueError(
                "Enter follow-up days as whole numbers separated by commas."
            ) from error
        active = fields.get("active") == "true"
        if active and country not in DRAFT_ONLY_COUNTRIES:
            from app.db.models import WorkspaceSetting
            from app.db.outreach_models import Mailbox
            from app.sending.campaign_sender import SENDER_KEY_PREFIX

            with services(request).db.sessions() as session:
                sender = session.get(WorkspaceSetting, SENDER_KEY_PREFIX + campaign_id)
                email = sender.value.get("email") if sender else None
                if not email or not session.scalar(
                    select(Mailbox.id).where(Mailbox.email == email)
                ):
                    raise ValueError("Choose a sending mailbox before activating email outreach.")
        edit_campaign(
            services(request).db,
            campaign_id,
            int(fields.get("version", "0")),
            principal.user.id,
            name=fields.get("name", ""),
            industry=fields.get("industry", ""),
            area=fields.get("area", ""),
            country=country,
            services=campaign_services(fields.get("services", "")),
            daily_limit=daily_limit,
            active=active,
            followup_days=followup_days,
        )
    except (ValueError, HTTPException) as error:
        from app.api.command import render_campaigns

        return render_campaigns(
            request,
            principal,
            form_context={
                "form_error": str(error.detail) if isinstance(error, HTTPException) else str(error),
                "form_error_title": "Campaign targeting could not be saved",
                "targeting_form_campaign": campaign_id,
                "targeting_form": fields,
            },
            status=error.status_code if isinstance(error, HTTPException) else 422,
        )
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
    fields: dict[str, str] = {}
    try:
        fields = await read_form(request)
        check_csrf(principal, fields.get("csrf_token", ""))
        save_campaign_sender(
            services(request).db,
            campaign_id,
            fields.get("mailbox_id", ""),
            int(fields.get("version", "0")),
            principal.user.id,
            from_name=fields.get("from_name"),
        )
    except (HTTPException, SettingsConflict, ValueError) as error:
        from app.api.command import render_campaigns

        return render_campaigns(
            request,
            principal,
            form_context={
                "form_error": str(error.detail) if isinstance(error, HTTPException) else str(error),
                "form_error_title": "Campaign sender could not be saved",
                "sender_form_campaign": campaign_id,
                "sender_form": fields,
            },
            status=(
                error.status_code
                if isinstance(error, HTTPException)
                else 409
                if isinstance(error, SettingsConflict)
                else 422
            ),
        )
    return RedirectResponse(f"/leads/campaigns#campaign-sender-{campaign_id}", 303)


@router.post("/campaigns/{campaign_id}/archive", response_model=None)
async def hide_campaign(
    request: Request, principal: Owner, campaign_id: str
) -> HTMLResponse | RedirectResponse:
    """Archive a redundant draft from the campaign directory."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        archive_campaign(
            services(request).db,
            campaign_id,
            principal.user.id,
            restore=fields.get("restore") == "true",
        )
    except ValueError as error:
        from app.api.command import render_campaigns

        return render_campaigns(
            request,
            principal,
            form_context={
                "form_error": str(error),
                "form_error_title": "Campaign could not be archived",
                "targeting_form_campaign": campaign_id,
                "targeting_form": {},
            },
            status=422,
        )
    return RedirectResponse("/leads/campaigns", 303)


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
