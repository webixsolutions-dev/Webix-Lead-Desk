"""Owner-queued research and reviewer observations on actual lead records."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.core.clock import utc_now
from app.db.lead_models import Company, CompanyDomain
from app.db.research_models import Evidence
from app.jobs.queue import enqueue
from app.sources.validation import domain_of, website_url

router = APIRouter()


@router.post("/leads/{company_id}/audit")
async def queue_audit(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Queue bounded website research only after a reviewer confirms business identity."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    db = services(request).db
    with db.sessions() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "Business not found")
        match = (
            session.scalar(
                select(CompanyDomain).where(
                    CompanyDomain.company_id == company_id,
                    CompanyDomain.domain == domain_of(company.website),
                    CompanyDomain.status == "verified",
                )
            )
            if company.website
            else None
        )
        if not match:
            raise HTTPException(422, "Confirm the business website match first")
    now = utc_now()
    enqueue(
        db,
        f"audit:{company_id}:{now.strftime('%Y%m%d%H%M')}",
        "audit",
        {"company_id": company_id, "force": fields.get("force") == "true"},
        now,
    )
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/leads/{company_id}/observation")
async def add_observation(
    request: Request, principal: CurrentUser, company_id: str
) -> RedirectResponse:
    """Save attributable manual visual, social or ads evidence without fetching social sites."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        kind = fields.get("kind", "")
        source = website_url(fields.get("source_url", ""))
        note = fields.get("note", "").strip()
        observed = datetime.fromisoformat(fields.get("observed_at", "")).replace(tzinfo=UTC)
        if (
            kind not in {"visual", "social", "ads", "business"}
            or not source
            or not 10 <= len(note) <= 2000
            or observed > utc_now()
        ):
            raise ValueError("Provide a source, observation date and factual note")
        with services(request).db.sessions.begin() as session:
            if session.get(Company, company_id) is None:
                raise HTTPException(404, "Business not found")
            session.add(
                Evidence(
                    company_id=company_id,
                    kind=kind,
                    source_url=source,
                    facts={"note": note},
                    reviewed_by=principal.user.id,
                    observed_at=observed,
                )
            )
    except ValueError as error:
        raise HTTPException(422, "Check the source URL, date and factual note") from error
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/leads/{company_id}/assess")
async def queue_assessment(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Queue optional model classification only when explicitly configured."""
    from app.db.lead_models import Campaign, CampaignLead

    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    app = services(request)
    if not app.settings.openai_enabled:
        raise HTTPException(422, "AI is disabled. Add verified funding and API settings first.")
    campaign_id = fields.get("campaign_id", "")
    with app.db.sessions() as session:
        campaign = session.get(Campaign, campaign_id)
        membership = session.scalar(
            select(CampaignLead.id).where(
                CampaignLead.company_id == company_id, CampaignLead.campaign_id == campaign_id
            )
        )
        if not campaign or not campaign.active or not membership:
            raise HTTPException(422, "Choose an active campaign for this business")
    now = utc_now()
    enqueue(
        app.db,
        f"assess:{company_id}:{now.strftime('%Y%m%d%H%M')}",
        "assess",
        {"company_id": company_id, "campaign_id": campaign_id},
        now,
    )
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/settings/ai-funding")
async def verify_funding(request: Request, principal: Owner) -> RedirectResponse:
    """Record a verified lifetime API allowance, preserving all committed reservations."""
    from decimal import Decimal, InvalidOperation

    from app.db.models import AuditEvent
    from app.db.research_models import BudgetAccount

    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        allowance = int(Decimal(fields.get("allowance_usd", "")) * 1_000_000)
        expires = datetime.fromisoformat(fields.get("expires_at", "")).replace(tzinfo=UTC)
        if (
            not 0 <= allowance <= 1_000_000_000
            or expires <= utc_now()
            or fields.get("verified") != "true"
        ):
            raise ValueError("Verify funding and expiry in the provider account first")
        with services(request).db.sessions.begin() as session:
            account = session.get(BudgetAccount, "primary")
            if account is None:
                account = BudgetAccount(
                    key="primary",
                    committed=0,
                    verified_by=principal.user.id,
                    expires_at=expires,
                    allowance=allowance,
                )
                session.add(account)
            if allowance < account.committed:
                raise ValueError("Allowance cannot be below existing commitments")
            account.allowance, account.expires_at = allowance, expires
            account.verified_by, account.verified_at = principal.user.id, utc_now()
            session.add(AuditEvent(actor_id=principal.user.id, action="ai.funding_verified"))
    except (ValueError, InvalidOperation, OverflowError) as error:
        raise HTTPException(422, "Verify the allowance, expiry and funding confirmation") from error
    return RedirectResponse("/settings", 303)
