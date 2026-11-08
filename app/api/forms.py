"""Manual contact-form preparation and review; no third-party form submission endpoint."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.db.form_models import FormDraft
from app.db.lead_models import Campaign, CampaignLead, Company
from app.db.outreach_models import Mailbox
from app.partners.forms import approve_form, form_block, record_form, save_form

router = APIRouter()


@router.get("/leads/{company_id}/form", response_class=HTMLResponse)
def form_page(request: Request, principal: Owner, company_id: str) -> HTMLResponse:
    """Show copy, permission evidence and manual submission history for one business."""
    context = dashboard_context(request, principal, "leads")
    with services(request).db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(404, "Business not found")
        context.update(
            company_record=company,
            form_draft=session.scalar(
                select(FormDraft)
                .where(FormDraft.company_id == company_id)
                .order_by(FormDraft.version.desc())
                .limit(1)
            ),
            campaigns=list(
                session.scalars(
                    select(Campaign).join(CampaignLead).where(CampaignLead.company_id == company_id)
                )
            ),
            mailboxes=list(session.scalars(select(Mailbox))),
        )
    context["form_block"] = (
        form_block(services(request).db, context["form_draft"]) if context["form_draft"] else None
    )
    return render(request, "form_outreach.html", context)


@router.post("/leads/{company_id}/form")
async def save_form_copy(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Save a new unapproved manual form revision."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        save_form(
            services(request).db,
            company_id,
            fields.get("campaign_id", ""),
            fields.get("mailbox_id", ""),
            fields.get("form_url", ""),
            fields.get("body", ""),
            fields.get("review_note", ""),
            int(fields.get("version", "0")),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}/form", 303)


@router.post("/forms/{form_id}/review")
async def review_form_copy(request: Request, principal: Owner, form_id: str) -> RedirectResponse:
    """Approve the displayed revision, or record an actual owner's manual outcome."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("reviewed") != "true":
        raise HTTPException(422, "Confirm this exact review or actual outcome")
    try:
        if fields.get("action") == "approve":
            approve_form(services(request).db, form_id, fields.get("digest", ""), principal.user.id)
        else:
            record_form(
                services(request).db,
                form_id,
                fields.get("action", ""),
                fields.get("note", ""),
                principal.user.id,
            )
        with services(request).db.sessions() as session:
            item = session.get(FormDraft, form_id)
            assert item
            company_id = item.company_id
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}/form", 303)
