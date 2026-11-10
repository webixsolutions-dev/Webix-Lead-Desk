"""Verified people, member registration and human cross-channel reply capture."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.db.lead_models import Campaign, CampaignLead, Company
from app.db.linkedin_models import LinkedInProfile, Person, PersonCandidate, PersonOwnership, Touch
from app.db.linkedin_queue_models import LinkedInPolicy
from app.db.models import User, new_id
from app.jobs.queue import enqueue
from app.linkedin.identity import add_profile, assign_person, confirm_person, release_company
from app.linkedin.quotas import Quotas
from app.linkedin.replies import log_reply, reopen_company

router = APIRouter()


@router.get("/settings/linkedin", response_class=HTMLResponse)
def profiles(request: Request, principal: Owner) -> HTMLResponse:
    """Register real staff identities without passwords or third-party cookies."""
    context = dashboard_context(request, principal, "settings")
    with services(request).db.sessions() as session:
        context.update(
            profiles=list(session.scalars(select(LinkedInProfile))),
            staff=list(session.scalars(select(User).where(User.active.is_(True)))),
            quota_values={
                p.profile_id: Quotas.model_validate(p.limits).model_dump()
                for p in session.scalars(select(LinkedInPolicy))
            },
            quota_defaults=Quotas().model_dump(),
        )
    return render(request, "linkedin_profiles.html", context)


@router.post("/settings/linkedin/profile")
async def create_profile(request: Request, principal: Owner) -> RedirectResponse:
    """Owner registers one profile belonging to one existing staff account."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("confirmed") != "true":
        raise HTTPException(422, "Confirm this is the member's own real profile")
    try:
        add_profile(
            services(request).db,
            fields.get("user_id", ""),
            fields.get("profile_url", ""),
            principal.user.id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/settings/linkedin", 303)


@router.get("/leads/{company_id}/people", response_class=HTMLResponse)
def people(request: Request, principal: Owner, company_id: str) -> HTMLResponse:
    """Review search proposals separately from confirmed people and ownership."""
    context = dashboard_context(request, principal, "leads")
    with services(request).db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(404, "Business not found")
        records = list(session.scalars(select(Person).where(Person.company_id == company_id)))
        context.update(
            company_record=company,
            people=records,
            ownerships={p.id: session.get(PersonOwnership, p.id) for p in records},
            candidates=list(
                session.scalars(
                    select(PersonCandidate).where(
                        PersonCandidate.company_id == company_id,
                        PersonCandidate.state == "proposed",
                    )
                )
            ),
            profiles=list(session.scalars(select(LinkedInProfile))),
            campaigns=list(
                session.scalars(
                    select(Campaign).join(CampaignLead).where(CampaignLead.company_id == company_id)
                )
            ),
            request_key=new_id(),
            touches=list(
                session.scalars(
                    select(Touch)
                    .where(Touch.company_id == company_id)
                    .order_by(Touch.occurred_at.desc())
                    .limit(30)
                )
            ),
        )
    return render(request, "linkedin_people.html", context)


@router.post("/leads/{company_id}/find-owner")
async def find_owner(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Queue a bounded web search; do not visit or scrape LinkedIn pages."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions() as session:
        if not session.get(Company, company_id):
            raise HTTPException(404, "Business not found")
    enqueue(
        services(request).db,
        "owner:" + company_id + ":" + new_id(),
        "find_owner",
        {
            "company_id": company_id,
            "campaign_id": fields.get("campaign_id", ""),
            "actor_id": principal.user.id,
        },
        utc_now(),
    )
    return RedirectResponse(f"/leads/{company_id}/people", 303)


@router.post("/leads/{company_id}/people")
async def verify_person(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Require a human name, role and profile match before creating a person."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if fields.get("confirmed") != "true":
        raise HTTPException(422, "Confirm the person works at this business")
    try:
        confirm_person(
            services(request).db,
            company_id,
            principal.user.id,
            name=fields.get("name", ""),
            role=fields.get("role", ""),
            url=fields.get("url", ""),
            email=fields.get("email", ""),
            source_url=fields.get("source_url", ""),
            note=fields.get("note", ""),
            confidence=int(fields.get("confidence", "0")),
            presence=fields.get("presence", "unknown"),
            decision_maker=fields.get("decision_maker") == "true",
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}/people", 303)


@router.post("/people/{person_id}/assign")
async def assign(request: Request, principal: Owner, person_id: str) -> RedirectResponse:
    """Record an owner-selected member, including existing first-degree connections."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        assign_person(
            services(request).db,
            person_id,
            fields.get("profile_id", ""),
            principal.user.id,
            warm=fields.get("warm") == "true",
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/leads", 303)


@router.post("/people/{person_id}/reply")
async def record_reply(
    request: Request, principal: CurrentUser, person_id: str
) -> RedirectResponse:
    """Log real replies with ownership authorization and idempotent stop semantics."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        log_reply(
            services(request).db,
            person_id,
            principal.user.id,
            fields.get("request_key", ""),
            fields.get("outcome", ""),
            fields.get("text", ""),
            channel=fields.get("channel", "linkedin"),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(
        f"/linkedin/conversations/{person_id}"
        if principal.user.role == "owner"
        else "/linkedin/queue",
        303,
    )


@router.post("/leads/{company_id}/linkedin-control")
async def control(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Owner may release routing or review an expired cooldown; never clear opt-outs."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        if fields.get("action") == "release":
            release_company(
                services(request).db, company_id, principal.user.id, fields.get("reason", "")
            )
        elif fields.get("action") == "reopen":
            reopen_company(
                services(request).db, company_id, principal.user.id, fields.get("reason", "")
            )
        else:
            raise ValueError("Choose a supported owner decision")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse(f"/leads/{company_id}/people", 303)
