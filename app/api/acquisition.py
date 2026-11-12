"""Authenticated controls for discovery, scoring and reviewed listing evidence."""

import difflib
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.audit.provider_pause import PROVIDERS, paused_event
from app.calls.listing import ListingReview, review_listing
from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.core.vault import store_secret
from app.db.acquisition_models import SearchRecord
from app.db.delivery_models import IntegrationSecret
from app.db.lead_models import Campaign, CampaignLead, Company
from app.db.models import AuditEvent
from app.db.outreach_models import Draft, Offer
from app.jobs.queue import enqueue
from app.scoring.service import LABELS, Rules, current_rules, save_rules
from app.sources.enrichment.config import SearchConfig, get_config, save_config

router = APIRouter()


def settings_data(request: Request) -> dict[str, Any]:
    """Return non-secret current search configuration and versioned scoring rules."""
    config, version = get_config(services(request).db)
    rules, rules_version = current_rules(services(request).db)
    with services(request).db.sessions() as session:
        paused = [
            {"provider": key, "label": label, "id": event.id, "at": event.created_at}
            for key, label in PROVIDERS.items()
            if (event := paused_event(session, key))
        ]
        pagespeed_connected = session.get(IntegrationSecret, "pagespeed") is not None
    return {
        "search_config": config,
        "search_version": version,
        "score_rules": rules.model_dump(),
        "score_version": rules_version,
        "score_labels": LABELS,
        "paused_providers": paused,
        "pagespeed_connected": pagespeed_connected,
    }


@router.post("/settings/api-resume")
async def resume_api(request: Request, principal: Owner) -> RedirectResponse:
    """Acknowledge a failure for new requests without rerunning any failed jobs."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    provider = fields.get("provider", "")
    if provider not in PROVIDERS:
        raise HTTPException(422, "Choose a paused provider")
    with services(request).db.sessions.begin() as session:
        event = paused_event(session, provider)
        if not event or event.id != fields.get("pause_id"):
            raise HTTPException(409, "Provider state changed; reload before resuming")
        session.add(
            AuditEvent(
                actor_id=principal.user.id,
                action="api.resumed",
                entity_id=provider,
                details={"pause_id": event.id},
            )
        )
    return RedirectResponse("/settings#acquisition", 303)


@router.post("/settings/acquisition")
async def acquisition_settings(request: Request, principal: Owner) -> RedirectResponse:
    """Save explicit limits and optional encrypted Places key without making any API call."""
    fields = await read_form(request, max_fields=32)
    check_csrf(principal, fields.pop("csrf_token", ""))
    key = fields.pop("places_key", "").strip()
    try:
        version = int(fields.pop("version", "0"))
        fields["enabled"] = fields.get("enabled", "false")
        fields["places_enabled"] = fields.get("places_enabled", "false")
        for name in ("monthly_usd", "search_usd", "places_monthly_usd", "places_search_usd"):
            if name in fields:
                amount = Decimal(fields.pop(name))
                fields[name + "_micro"] = str(int(amount * 1_000_000))
        config = SearchConfig.model_validate(fields)
        if config.no_website_offer_id:
            with services(request).db.sessions() as session:
                offer = session.get(Offer, config.no_website_offer_id)
                if not offer or not offer.active:
                    raise ValueError("Select an active Offer Library entry")
        if key:
            store_secret(
                services(request).db,
                services(request).settings,
                "google_places",
                key,
                principal.user.id,
            )
        save_config(services(request).db, config, version, principal.user.id)
    except SettingsConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(
            422, "Check search limits, selected offer and connection configuration"
        ) from error
    return RedirectResponse("/settings#acquisition", 303)


@router.post("/settings/score-rules")
async def score_settings(request: Request, principal: Owner) -> RedirectResponse:
    """Version the weights and queue offline recalculation for existing campaign leads."""
    fields = await read_form(request)
    check_csrf(principal, fields.pop("csrf_token", ""))
    try:
        version = int(fields.pop("version", "0"))
        save_rules(services(request).db, Rules.model_validate(fields), version, principal.user.id)
    except SettingsConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, "Check point values and ordered score thresholds") from error
    with services(request).db.sessions() as session:
        members = list(session.scalars(select(CampaignLead)))
    for member in members:
        enqueue(
            services(request).db,
            f"rescore:{version + 1}:{member.id}",
            "score",
            {"company_id": member.company_id, "campaign_id": member.campaign_id},
            utc_now(),
        )
    return RedirectResponse("/settings#scoring", 303)


@router.post("/sources/discover")
async def discover_campaign(request: Request, principal: Owner) -> RedirectResponse:
    """Start any-city campaign search through the selected budget-gated provider."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    campaign_id = fields.get("campaign_id", "")
    try:
        batches = int(fields.get("batches", "1"))
        if not 1 <= batches <= 10:
            raise ValueError("Batch count")
    except ValueError as error:
        raise HTTPException(422, "Choose 1–10 discovery batches") from error
    with services(request).db.sessions() as session:
        campaign = session.get(Campaign, campaign_id)
        if not campaign or not campaign.active:
            raise HTTPException(422, "Choose an active campaign")
    enqueue(
        services(request).db,
        f"discover:{campaign_id}:{campaign.version}:{utc_now():%Y%m%d%H%M}",
        "discover",
        {"campaign_id": campaign_id, "actor_id": principal.user.id, "remaining_batches": batches},
        utc_now(),
    )
    return RedirectResponse("/outbox", 303)


@router.post("/leads/{company_id}/enrich")
async def enrich(request: Request, principal: Owner, company_id: str) -> RedirectResponse:
    """Queue a reviewable enrichment request; the worker enforces the 90-day marker."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    with services(request).db.sessions() as session:
        member = session.scalar(
            select(CampaignLead).where(
                CampaignLead.company_id == company_id,
                CampaignLead.campaign_id == fields.get("campaign_id", ""),
            )
        )
        if not member:
            raise HTTPException(422, "Choose this business's campaign")
    enqueue(
        services(request).db,
        f"manual_enrich:{member.id}:{utc_now():%Y%m%d}",
        "enrich_company",
        {
            "company_id": company_id,
            "campaign_id": member.campaign_id,
            "actor_id": principal.user.id,
        },
        utc_now(),
    )
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.post("/leads/{company_id}/listing")
async def listing(request: Request, principal: CurrentUser, company_id: str) -> RedirectResponse:
    """Record reviewer-confirmed listing facts and explicit known/unknown scoring signals."""
    fields = await read_form(request)
    check_csrf(principal, fields.pop("csrf_token", ""))
    data: dict[str, Any] = dict(fields)
    data["signals"] = {
        name: fields[name] == "true" for name in LABELS if fields.get(name) in {"true", "false"}
    }
    for name in LABELS:
        data.pop(name, None)
    for name in ("rating", "review_count"):
        if not data.get(name):
            data[name] = None
    try:
        review_listing(
            services(request).db, company_id, ListingReview.model_validate(data), principal.user.id
        )
    except ValueError as error:
        raise HTTPException(
            422, "Check the listing URL, observations and website status"
        ) from error
    return RedirectResponse(f"/leads/{company_id}", 303)


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_revision(request: Request, principal: CurrentUser, draft_id: str) -> HTMLResponse:
    """Show the exact immutable revision and an escaped text diff for timeline links."""
    with services(request).db.sessions() as session:
        item = session.get(Draft, draft_id)
        if not item:
            raise HTTPException(404, "Draft not found")
        previous = session.scalar(
            select(Draft)
            .where(
                Draft.conversation_id == item.conversation_id,
                Draft.step == item.step,
                Draft.revision < item.revision,
            )
            .order_by(Draft.revision.desc())
            .limit(1)
        )
    context = dashboard_context(request, principal, "outbox")
    context.update(
        revision=item,
        diff="\n".join(
            difflib.unified_diff(
                previous.snapshot["body"].splitlines() if previous else [],
                item.snapshot["body"].splitlines(),
                fromfile="Previous revision",
                tofile="This revision",
                lineterm="",
            )
        ),
    )
    return render(request, "draft_revision.html", context)


def company_data(request: Request, company_id: str) -> dict[str, Any]:
    """Add search provenance and journey projections without exposing provider credentials."""
    from app.calls.board import offer_for_listing
    from app.journey.project import detail

    with services(request).db.sessions() as session:
        searches = list(
            session.scalars(
                select(SearchRecord)
                .where(SearchRecord.company_id == company_id)
                .order_by(SearchRecord.created_at.desc())
                .limit(5)
            )
        )
        company = session.get(Company, company_id)
    offer = offer_for_listing(services(request).db)
    return {
        "journey": detail(services(request).db, company_id),
        "searches": searches,
        "score_labels": LABELS,
        "listing_offer": offer,
        "manual_dm": f"Hi {company.name}, would a free homepage concept be useful to review?"
        if company and company.has_website == "false" and offer
        else "",
    }
