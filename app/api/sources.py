"""Authenticated campaigns, imports and evidence-based contact review."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, Principal, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.lead_models import (
    Campaign,
    CampaignLead,
    CampaignRun,
    Company,
    CompanyDomain,
    Contact,
    ContactBasis,
    SourceRecord,
    SuppressionEntry,
)
from app.db.models import AuditEvent
from app.jobs.queue import enqueue
from app.policy.contacts import suppress
from app.sources.importing import import_candidates, parse_csv
from app.sources.validation import domain_of, website_url

router = APIRouter()


def lead_context(request: Request, principal: Principal) -> dict[str, Any]:
    """Read a bounded page of actual records and associated campaign choices."""
    context = dashboard_context(request, principal, "leads")
    with services(request).db.sessions() as session:
        query = select(Company).order_by(Company.created_at.desc())
        campaign_id = request.query_params.get("campaign", "")
        search = request.query_params.get("q", "").strip()[:120]
        if campaign_id:
            query = query.join(CampaignLead).where(CampaignLead.campaign_id == campaign_id)
        if search:
            query = query.where(Company.name.icontains(search, autoescape=True))
        context.update(
            companies=list(session.scalars(query.limit(100))),
            campaigns=list(
                session.scalars(select(Campaign).order_by(Campaign.created_at.desc()).limit(100))
            ),
            imports=list(
                session.scalars(
                    select(CampaignRun).order_by(CampaignRun.created_at.desc()).limit(10)
                )
            ),
            suppressions=list(
                session.scalars(
                    select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc()).limit(50)
                )
            ),
            selected_campaign=campaign_id,
            search=search,
        )
    return context


@router.post("/campaigns")
async def create_campaign(request: Request, principal: Owner) -> RedirectResponse:
    """Create a targeting configuration without enabling any external spending."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        name, industry, area = (fields.get(key, "").strip() for key in ("name", "industry", "area"))
        options = [
            value.strip() for value in fields.get("services", "").split(",") if value.strip()
        ]
        lane = fields.get("lane", "client")
        limit = int(fields.get("daily_limit", "10"))
        if (
            not all(1 <= len(v) <= 120 for v in (name, industry, area))
            or not options
            or len(options) > 12
            or any(len(v) > 120 for v in options)
        ):
            raise ValueError("Complete the campaign fields")
        if lane not in {"client", "partner"} or not 1 <= limit <= (5 if lane == "partner" else 50):
            raise ValueError("Invalid campaign lane or limit")
        with services(request).db.sessions.begin() as session:
            campaign = Campaign(
                name=name,
                industry=industry,
                area=area,
                services=options,
                lane=lane,
                daily_limit=limit,
            )
            session.add(campaign)
            session.flush()
            campaign_id = campaign.id
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads?campaign={campaign_id}", 303)


@router.post("/sources/csv")
async def upload_csv(request: Request, principal: Owner) -> RedirectResponse:
    """Validate an uploaded or pasted CSV and import it in one transaction."""
    fields = await read_form(request, maximum=1_100_000)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        rows = parse_csv(fields.get("csv_text", "").encode())
        import_candidates(
            services(request).db, fields.get("campaign_id", ""), rows, "csv", principal.user.id
        )
    except ValueError as error:
        raise HTTPException(422, "CSV invalid: check columns, row count and public URLs") from error
    except IntegrityError as error:
        raise HTTPException(
            409, "A concurrent import changed these records. Retry the import."
        ) from error
    return RedirectResponse("/leads", 303)


@router.post("/sources/calgary")
async def import_calgary(request: Request, principal: Owner) -> RedirectResponse:
    """Queue free city-data discovery for the separate worker."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    campaign_id = fields.get("campaign_id", "")
    with services(request).db.sessions() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None or not campaign.active:
            raise HTTPException(422, "Choose an active campaign")
    category = fields.get("category", "").strip()
    if not 2 <= len(category) <= 80:
        raise HTTPException(422, "Enter a city licence category")
    now = utc_now()
    enqueue(
        services(request).db,
        f"calgary:{campaign_id}:{now.strftime('%Y%m%d%H%M')}",
        "calgary",
        {"campaign_id": campaign_id, "category": category, "actor_id": principal.user.id},
        now,
    )
    return RedirectResponse("/outbox", 303)


@router.get("/leads/{company_id}", response_class=HTMLResponse)
def company_detail(request: Request, principal: CurrentUser, company_id: str) -> HTMLResponse:
    """Show private source and review records for one actual business."""
    context = dashboard_context(request, principal, "leads")
    with services(request).db.sessions() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Business not found")
        context.update(
            company_record=company,
            domains=list(
                session.scalars(select(CompanyDomain).where(CompanyDomain.company_id == company_id))
            ),
            contacts=list(session.scalars(select(Contact).where(Contact.company_id == company_id))),
            sources=list(
                session.scalars(select(SourceRecord).where(SourceRecord.company_id == company_id))
            ),
        )
    return render(request, "company.html", context)


@router.post("/leads/{company_id}/website")
async def review_website(
    request: Request, principal: CurrentUser, company_id: str
) -> RedirectResponse:
    """Record a person's identity-match decision with the supporting reason."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        url = website_url(fields.get("website", ""))
        note = fields.get("note", "").strip()
        if not url or not 10 <= len(note) <= 2000:
            raise ValueError("A website and matching evidence are required")
        with services(request).db.sessions.begin() as session:
            company = session.get(Company, company_id)
            if company is None:
                raise HTTPException(404, "Business not found")
            domain = session.scalar(
                select(CompanyDomain).where(
                    CompanyDomain.company_id == company_id, CompanyDomain.domain == domain_of(url)
                )
            )
            if domain is None:
                domain = CompanyDomain(company_id=company_id, domain=domain_of(url))
                session.add(domain)
            company.website = url
            domain.status, domain.review_note = "verified", note
            domain.reviewed_by, domain.reviewed_at = principal.user.id, utc_now()
            session.add(
                AuditEvent(
                    actor_id=principal.user.id,
                    action="company.website_review",
                    entity_id=company_id,
                )
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/contacts/{contact_id}/basis")
async def review_contact(request: Request, principal: Owner, contact_id: str) -> RedirectResponse:
    """Capture role relevance and source review; this alone never approves a message."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        source = website_url(fields.get("source_url", ""))
        relevance = fields.get("relevance", "").strip()
        if not source or not 10 <= len(relevance) <= 2000:
            raise ValueError("Provide the publication URL and role relevance")
        with services(request).db.sessions.begin() as session:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise HTTPException(404, "Contact not found")
            now = utc_now()
            session.add(
                ContactBasis(
                    contact_id=contact_id,
                    kind="publication",
                    source_url=source,
                    relevance=relevance,
                    no_solicitation=fields.get("no_solicitation") == "true",
                    reviewed_by=principal.user.id,
                    observed_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
            company_id = contact.company_id
        if fields.get("no_solicitation") == "true":
            suppress(
                services(request).db, contact.email, "Publication prohibits unsolicited messages"
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/suppression")
async def add_suppression(request: Request, principal: Owner) -> RedirectResponse:
    """Add a global stop without offering a casual removal action."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        suppress(services(request).db, fields.get("value", ""), fields.get("reason", ""))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/leads", 303)
