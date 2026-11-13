"""Budgeted provider search and conservative first-party identity acceptance."""

import hashlib
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.audit.budget import BudgetBlocked
from app.core.ai_config import task_settings
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import openai_key, read_secret
from app.db.acquisition_models import SearchRecord
from app.db.lead_models import Campaign, Company, CompanyDomain
from app.db.research_models import Evidence
from app.db.session import Database
from app.integrations.openai import create_response
from app.integrations.places import search_places
from app.integrations.public_http import Page, fetch_public
from app.journey.events import emit
from app.sources.enrichment.config import get_config
from app.sources.enrichment.providers import (
    Provider,
    parse_openai,
    parse_places,
    reserve_cost,
    search_payload,
)
from app.sources.enrichment.quota import reserve_search, settle_search
from app.sources.enrichment.schema import BusinessResult, SearchResults
from app.sources.enrichment.validation import Match, validate_site
from app.sources.validation import domain_of


def search(
    db: Database,
    settings: Settings,
    campaign_id: str,
    query: str,
    request_key: str,
    company_id: str | None = None,
    *,
    provider: Provider = create_response,
    places_provider: Provider = search_places,
    linkedin: bool = False,
) -> tuple[SearchRecord, SearchResults]:
    """Call once after durable reservation; never repeat an uncertain or cached search."""
    config, _ = get_config(db)
    settings = task_settings(settings, "audit").model_copy(
        update={"openai_max_output_tokens": settings.openai_search_max_output_tokens}
    )
    with db.sessions() as session:
        campaign = session.get(Campaign, campaign_id)
        from app.linkedin.campaigns import active_campaign

        if not campaign or not (
            active_campaign(session, campaign_id) if linkedin else campaign.active
        ):
            raise ValueError("Choose an active campaign")
    if config.provider == "openai_web_search":
        if (
            not settings.openai_enabled
            or not settings.openai_model
            or min(
                settings.openai_input_usd_micro_per_million,
                settings.openai_output_usd_micro_per_million,
                config.search_usd_micro,
            )
            <= 0
        ):
            raise BudgetBlocked(
                "AI, model rates and the owner-entered web-search price are required"
            )
        key = openai_key(db, settings)
        payload = search_payload(settings, query)
        maximum = reserve_cost(settings, payload, config.search_usd_micro)
    else:
        key = read_secret(db, settings, "google_places")
        payload = {"textQuery": query, "pageSize": 10}
        maximum = config.places_search_usd_micro
    cache_key = (
        "company:" + company_id
        if company_id
        else "query:" + hashlib.sha256(query.casefold().encode()).hexdigest()
    )
    record = reserve_search(
        db,
        config,
        request_key,
        cache_key,
        company_id,
        campaign_id,
        settings.openai_model,
        maximum,
        utc_now(),
    )
    try:
        if config.provider == "openai_web_search":
            results, actual = parse_openai(
                provider(key, payload), settings, config.search_usd_micro
            )
        else:
            results = parse_places(places_provider(key, payload), campaign.area)
            actual = config.places_search_usd_micro
        # Never persist Google's transient names, addresses, review data or raw response.
        stored: dict[str, Any] = (
            results.model_dump()
            if config.provider == "openai_web_search"
            else {
                "place_ids": [item.place_id for item in results.businesses],
                "retention": "place_ids_only",
            }
        )
        with db.sessions.begin() as session:
            current = session.get(SearchRecord, record.id)
            assert current
            current.result = stored
        settle_search(db, record, actual)
        return record, results
    except Exception:
        settle_search(db, record, None)
        raise


def accept_result(
    db: Database,
    company_id: str,
    result: BusinessResult,
    search_id: str,
    *,
    provider: str = "openai_web_search",
    fetcher: Callable[[str], Page] = fetch_public,
    match: Match | None = None,
) -> bool:
    """Save only validated identity; ambiguous facts remain explicitly reviewable."""
    with db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            raise ValueError("Business no longer exists")
    match = match or validate_site(result, company.name, company.city, company.address, fetcher)
    with db.sessions.begin() as session:
        company = session.get(Company, company_id)
        assert company
        accepted = match.accepted
        if accepted:
            domain = domain_of(match.website)
            others = session.scalar(
                select(CompanyDomain.id).where(
                    CompanyDomain.domain == domain, CompanyDomain.company_id != company.id
                )
            )
            if others:
                accepted = False
            else:
                own = session.scalar(
                    select(CompanyDomain).where(
                        CompanyDomain.company_id == company.id, CompanyDomain.domain == domain
                    )
                )
                if not own:
                    own = CompanyDomain(company_id=company.id, domain=domain)
                    session.add(own)
                own.status, own.review_note, own.reviewed_at = "verified", match.reason, utc_now()
                company.website, company.has_website = match.website, "true"
                digits = re.sub(r"\D", "", result.phone)
                if digits and digits in re.sub(r"\D", "", match.text):
                    company.phone = result.phone
        if not accepted:
            company.status = "needs_review"
        facts: dict[str, Any] = {
            "accepted": accepted,
            "reason": match.reason
            if not others_if_shared(accepted, match)
            else "Shared domain requires review",
            "search_id": search_id,
        }
        if provider == "openai_web_search":
            facts["candidate"] = result.model_dump()
            # This is a sourced candidate, not a reviewer-confirmed listing or consent.
            if not company.phone and any(c.field == "phone" for c in result.sources):
                company.phone = result.phone
        elif accepted:
            facts["first_party_text"] = match.text[:2000]
            facts["place_id"] = result.place_id
        original = session.get(SearchRecord, search_id)
        if (
            original
            and original.company_id is None
            and not session.scalar(
                select(SearchRecord.id).where(
                    SearchRecord.request_key == f"seed:{search_id}:{company_id}"
                )
            )
        ):
            session.add(
                SearchRecord(
                    request_key=f"seed:{search_id}:{company_id}",
                    cache_key="company:" + company_id,
                    company_id=company_id,
                    campaign_id=original.campaign_id,
                    provider=provider,
                    state="complete",
                    reserved=0,
                    created_at=original.created_at,
                    result=result.model_dump()
                    if provider == "openai_web_search"
                    else {"place_id": result.place_id},
                )
            )
        evidence = Evidence(
            company_id=company.id,
            kind="enrichment",
            source_url=match.website if accepted else "",
            facts=facts,
        )
        session.add(evidence)
        session.flush()
        emit(
            session,
            company.id,
            f"enrich:{search_id}:{company.id}",
            "enriched" if accepted else "enrichment_review",
            {"reason": facts["reason"], "evidence_id": evidence.id, "website": company.website},
        )
    return accepted


def others_if_shared(accepted: bool, match: Match) -> bool:
    """Distinguish an identity match blocked by an already claimed domain."""
    return match.accepted and not accepted
