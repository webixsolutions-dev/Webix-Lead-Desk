"""Durable discovery → validation → audit → score → unapproved draft orchestration."""

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.acquisition_models import SearchRecord
from app.db.lead_models import Campaign, Company, CompanyDomain, Contact, SourceRecord
from app.db.models import Job
from app.db.outreach_models import Conversation, Mailbox
from app.db.research_models import WebsiteAudit
from app.db.session import Database
from app.integrations.openai import create_response
from app.integrations.places import search_places
from app.integrations.public_http import Page, fetch_public
from app.jobs.queue import enqueue
from app.journey.events import emit
from app.scoring.service import score
from app.sources.enrichment.config import get_config
from app.sources.enrichment.providers import Provider
from app.sources.enrichment.service import accept_result, search
from app.sources.enrichment.validation import validate_site, words
from app.sources.importing import import_candidates
from app.sources.validation import Candidate, domain_of, identity_key


def hold(db: Database, company_id: str, key: str, reason: str) -> str:
    """Expose a precise next action without modifying the journey's derived stage."""
    with db.sessions.begin() as session:
        emit(session, company_id, "flow_hold:" + key, "action_needed", {"reason": reason})
    return reason


def plan_import(db: Database, run_id: str, campaign_id: str, actor: str) -> None:
    """Queue deterministic free scoring and enrichment for newly sourced businesses."""
    with db.sessions() as session:
        ids = list(
            session.scalars(select(SourceRecord.company_id).where(SourceRecord.run_id == run_id))
        )
    for company_id in ids:
        score(db, company_id, campaign_id)
        enqueue(
            db,
            f"acquisition:{company_id}:{campaign_id}",
            "advance_company",
            {"company_id": company_id, "campaign_id": campaign_id, "actor_id": actor},
            utc_now(),
        )


def discover(
    db: Database,
    settings: Settings,
    job: Job,
    *,
    provider: Provider = create_response,
    places_provider: Provider = search_places,
    fetcher: Callable[[str], Page] = fetch_public,
) -> str:
    """Find candidates for any campaign city; domain matches require matching identity."""
    with db.sessions() as session:
        campaign = session.get(Campaign, job.payload["campaign_id"])
        if not campaign or not campaign.active:
            raise ValueError("Active campaign required")
    config, _ = get_config(db)
    with db.sessions() as session:
        known = list(
            session.scalars(
                select(Company.name)
                .where(Company.city.icontains(campaign.area))
                .order_by(Company.created_at.desc())
                .limit(200)
            )
        )
    query = (
        f"Find {campaign.industry} businesses in {campaign.area}, {campaign.country}. "
        f"Return at most {config.discovery_batch_size} new businesses. "
        "Exclude already recorded businesses: " + "; ".join(known)
    )
    record, results = search(
        db,
        settings,
        campaign.id,
        query,
        job.id,
        provider=provider,
        places_provider=places_provider,
    )
    count, added = 0, 0
    for result in results.businesses[: config.discovery_batch_size]:
        if not (
            words(result.city) in words(campaign.area) or words(campaign.area) in words(result.city)
        ):
            continue
        match = None
        if record.provider == "google_places":
            match = validate_site(result, result.name, result.city, result.address, fetcher)
            if not match.accepted:
                continue  # Retain only place_id, not a Google-derived business database.
        with db.sessions() as session:
            # Exact name/locality plus a shared domain can resolve a missing address;
            # unrelated franchise branches are not merged just because of a domain.
            existing = next(
                (
                    item
                    for item in session.scalars(
                        select(Company)
                        .join(CompanyDomain)
                        .where(CompanyDomain.domain == domain_of(result.website))
                    )
                    if result.website
                    and words(item.name) == words(result.name)
                    and words(item.city) == words(result.city)
                    and (
                        not item.address
                        or not result.address
                        or words(item.address) == words(result.address)
                    )
                ),
                None,
            )
        row = Candidate(
            name=existing.name if existing else result.name,
            city=existing.city if existing else result.city,
            industry=campaign.industry,
            address=existing.address
            if existing
            else (
                result.address if not match or words(result.address) in words(match.text) else ""
            ),
            source_key=existing.identity_key
            if existing
            else identity_key(result.name, result.city, result.address),
            source_url=match.website if match else result.sources[0].url if result.sources else "",
        )
        run = import_candidates(db, campaign.id, [row], record.provider, job.payload["actor_id"])
        with db.sessions() as session:
            source = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.source == record.provider,
                    SourceRecord.source_key == row.source_key,
                )
            )
            assert source
            company_id = source.company_id
        added += run.imported
        accepted = accept_result(
            db,
            company_id,
            result,
            record.id,
            provider=record.provider,
            fetcher=fetcher,
            match=match,
        )
        if accepted:
            enqueue(
                db,
                f"enriched:{record.id}:{company_id}",
                "advance_company",
                {
                    "company_id": company_id,
                    "campaign_id": campaign.id,
                    "actor_id": job.payload["actor_id"],
                },
                utc_now(),
            )
        else:
            hold(db, company_id, run.id, "Review the search identity and listing evidence")
        count += 1
    remaining = int(job.payload.get("remaining_batches", 1))
    if added and remaining > 1:
        root = str(job.payload.get("discovery_root", job.id))
        enqueue(
            db,
            f"discovery_batch:{root}:{remaining - 1}",
            "discover",
            {**job.payload, "discovery_root": root, "remaining_batches": remaining - 1},
            utc_now(),
        )
    return f"Found {count} businesses ({added} new); drafts require owner approval."


def enrich_company(
    db: Database,
    settings: Settings,
    job: Job,
    *,
    provider: Provider = create_response,
    places_provider: Provider = search_places,
    fetcher: Callable[[str], Page] = fetch_public,
) -> str:
    """Find one business's official site and published contact clues with citations."""
    with db.sessions() as session:
        company = session.get(Company, job.payload["company_id"])
        if not company:
            raise ValueError("Business not found")
        if company.website or company.has_website == "false":
            return "Website already recorded or reviewed as absent; no paid search repeated."
    record, result = search(
        db,
        settings,
        job.payload["campaign_id"],
        f"Exact business: {company.name}; city: {company.city}; address: {company.address}. "
        f"Find its official website and published contact details.",
        job.id,
        company.id,
        provider=provider,
        places_provider=places_provider,
    )
    if len(result.businesses) != 1:
        return hold(db, company.id, job.id, "Search returned no unique business; review candidates")
    if accept_result(
        db, company.id, result.businesses[0], record.id, provider=record.provider, fetcher=fetcher
    ):
        enqueue(db, "enriched:" + record.id, "advance_company", job.payload, utc_now())
        return "Official website verified; website audit queued."
    return hold(db, company.id, job.id, "Website identity needs human review")


def advance(db: Database, settings: Settings, job: Job) -> str:
    """Continue only through evidence-backed stages; never create send approval."""
    from app.linkedin.coordination import outreach_block

    company_id, campaign_id = job.payload["company_id"], job.payload["campaign_id"]
    with db.sessions() as session:
        blocked = outreach_block(session, company_id)
    if blocked:
        return hold(db, company_id, job.id, blocked)
    score(db, company_id, campaign_id)
    with db.sessions() as session:
        company = session.get(Company, company_id)
        campaign = session.get(Campaign, campaign_id)
        if not company or not campaign or not campaign.active:
            raise ValueError("Business and active campaign required")
        audit = session.scalar(
            select(WebsiteAudit)
            .where(WebsiteAudit.company_id == company_id, WebsiteAudit.website == company.website)
            .order_by(WebsiteAudit.created_at.desc())
            .limit(1)
        )
        contacts = list(session.scalars(select(Contact).where(Contact.company_id == company_id)))
        mailboxes = list(
            session.scalars(
                select(Mailbox).where(Mailbox.lane == campaign.lane, Mailbox.enabled.is_(True))
            )
        )
        conversation = session.scalar(
            select(Conversation.id).where(Conversation.company_id == company_id)
        )
    if company.has_website == "false":
        return hold(
            db,
            company_id,
            job.id,
            "Review the listing, then use the human call list or manual DM draft",
        )
    if not company.website:
        with db.sessions() as session:
            recent = session.scalar(
                select(SearchRecord.id)
                .where(
                    SearchRecord.cache_key == "company:" + company_id,
                    SearchRecord.created_at > utc_now() - timedelta(days=90),
                )
                .limit(1)
            )
        if recent:
            return hold(
                db,
                company_id,
                job.id,
                "Search is cached for 90 days; review the existing candidate evidence",
            )
        enqueue(
            db, f"enrich:{company_id}:{utc_now():%Y%m%d}", "enrich_company", job.payload, utc_now()
        )
        return "Company enrichment queued."
    if not audit:
        enqueue(
            db,
            f"flow_audit:{company_id}:{campaign_id}",
            "audit",
            {**job.payload, "continue_flow": True},
            utc_now(),
        )
        return "Website audit queued."
    if audit.state not in {"complete", "partial"}:
        return hold(db, company_id, job.id, "Website audit is blocked; review identity or listing")
    if not contacts:
        return hold(
            db, company_id, job.id, "No email extracted; use human Calls if a phone is available"
        )
    if conversation:
        return "Existing conversation preserved; review its current draft or reply."
    if len(mailboxes) != 1:
        return hold(
            db,
            company_id,
            job.id,
            "Choose the sender in Write a draft; automatic drafting requires one eligible "
            "sender in this lane",
        )
    if not settings.openai_enabled:
        return hold(
            db,
            company_id,
            job.id,
            "Rule score ready; enable configured AI or write a draft manually",
        )
    payload = {**job.payload, "contact_id": contacts[0].id, "mailbox_id": mailboxes[0].id}
    enqueue(
        db,
        f"flow_assess:{company_id}:{campaign_id}",
        "assess",
        {**payload, "continue_flow": True},
        utc_now(),
    )
    return "AI assessment queued, then an unapproved personalized draft."
