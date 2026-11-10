"""Authenticated campaigns, imports and evidence-based contact review."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, Principal, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.timezones import zone
from app.db.acquisition_models import LeadScore
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
from app.db.research_models import Assessment, Evidence, WebsiteAudit
from app.jobs.queue import enqueue
from app.journey.project import summary
from app.playbook.catalog import service_names
from app.policy.contacts import suppress
from app.sources.importing import import_candidates, parse_csv
from app.sources.validation import domain_of, website_url

router = APIRouter()


def lead_context(request: Request, principal: Principal) -> dict[str, Any]:
    """Read a bounded page of actual records and associated campaign choices."""
    context = dashboard_context(request, principal, "leads")
    with services(request).db.sessions() as session:
        latest_score = (
            select(LeadScore.points)
            .where(LeadScore.company_id == Company.id)
            .order_by(LeadScore.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = select(Company).order_by(
            latest_score.desc().nulls_last(), Company.created_at.desc()
        )
        if request.query_params.get("website") == "none":
            query = query.where(Company.has_website == "false")
        campaign_id = request.query_params.get("campaign", "")
        search = request.query_params.get("q", "").strip()[:120]
        if campaign_id:
            query = query.join(CampaignLead).where(CampaignLead.campaign_id == campaign_id)
        lane = request.query_params.get("lane", "")
        if lane in {"partner", "client"}:
            query = query.where(
                Company.id.in_(
                    select(CampaignLead.company_id).join(Campaign).where(Campaign.lane == lane)
                )
            )
        if search:
            query = query.where(Company.name.icontains(search, autoescape=True))
        companies = []
        journeys = {}
        for business in session.scalars(query.execution_options(yield_per=50)):
            journey = summary(session, business.id)
            if (
                request.query_params.get("stage")
                and journey["stage"] != request.query_params["stage"]
            ):
                continue
            if request.query_params.get("band") and (
                not journey["score"] or journey["score"].band != request.query_params["band"]
            ):
                continue
            if request.query_params.get("today") == "1" and not journey["needs_today"]:
                continue
            if request.query_params.get("since") and (
                not journey["last"]
                or journey["last"]
                .occurred_at.astimezone(zone("America/Edmonton"))
                .date()
                .isoformat()
                < request.query_params["since"]
            ):
                continue
            companies.append(business)
            journeys[business.id] = journey
            if len(companies) == 100:
                break
        context.update(
            companies=companies,
            journeys=journeys,
            no_website_count=session.scalar(
                select(func.count()).select_from(Company).where(Company.has_website == "false")
            )
            or 0,
            lead_filters=dict(request.query_params),
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
            service_names=service_names(session),
            selected_campaign=campaign_id,
            selected_lane=lane,
            search=search,
        )
    context["calgary_campaigns"] = [
        c for c in context["campaigns"] if c.country == "CA" and "calgary" in c.area.lower()
    ]
    return context


@router.post("/campaigns")
async def create_campaign(request: Request, principal: Owner) -> RedirectResponse:
    """Create a targeting configuration without enabling any external spending."""
    fields = await read_form(request, repeatable=frozenset({"services"}))
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        name, industry, area = (fields.get(key, "").strip() for key in ("name", "industry", "area"))
        options = [
            value.strip() for value in fields.get("services", "").split(",") if value.strip()
        ]
        lane = fields.get("lane", "client")
        country = fields.get("country", "CA")
        if country not in {"CA", "US"}:
            raise ValueError("Choose Canada or the US")
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
                country=country,
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
    if campaign.country != "CA" or "calgary" not in campaign.area.lower():
        raise HTTPException(422, "Open Calgary licence data requires a Calgary campaign")
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
            audits=list(
                session.scalars(
                    select(WebsiteAudit)
                    .where(WebsiteAudit.company_id == company_id)
                    .order_by(WebsiteAudit.created_at.desc())
                    .limit(5)
                )
            ),
            evidence=list(
                session.scalars(
                    select(Evidence)
                    .where(Evidence.company_id == company_id)
                    .order_by(Evidence.observed_at.desc())
                    .limit(30)
                )
            ),
            assessments=list(
                session.scalars(
                    select(Assessment)
                    .where(Assessment.company_id == company_id)
                    .order_by(Assessment.created_at.desc())
                    .limit(5)
                )
            ),
            campaigns=list(
                session.scalars(
                    select(Campaign).join(CampaignLead).where(CampaignLead.company_id == company_id)
                )
            ),
            domains=list(
                session.scalars(select(CompanyDomain).where(CompanyDomain.company_id == company_id))
            ),
            contacts=list(session.scalars(select(Contact).where(Contact.company_id == company_id))),
            sources=list(
                session.scalars(select(SourceRecord).where(SourceRecord.company_id == company_id))
            ),
        )
    from app.api.acquisition import company_data

    context.update(company_data(request, company_id))
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
            company.has_website = "true"
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
