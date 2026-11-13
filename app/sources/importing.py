"""Atomic, repeatable imports with global identity and source provenance."""

import csv
import io

from sqlalchemy import select

from app.core.clock import utc_now
from app.db.lead_models import (
    Campaign,
    CampaignLead,
    CampaignRun,
    Company,
    CompanyDomain,
    Contact,
    SourceRecord,
)
from app.db.models import AuditEvent
from app.db.session import Database
from app.sources.validation import Candidate, domain_of, identity_key


def parse_csv(raw: bytes) -> list[Candidate]:
    """Validate the entire bounded CSV before an import transaction can begin."""
    if len(raw) > 1_000_000:
        raise ValueError("CSV exceeds the 1 MB limit")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not {"name", "city", "industry"}.issubset(headers):
            raise ValueError("CSV needs unique name, city and industry columns")
        if set(headers) - Candidate.model_fields.keys():
            raise ValueError("CSV contains unsupported columns")
        rows = []
        for index, row in enumerate(reader):
            if index >= 500:
                raise ValueError("Import at most 500 rows at a time")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV rows must match the header")
            rows.append(Candidate.model_validate(row))
        if not rows:
            raise ValueError("CSV contains no candidates")
        return rows
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("Upload a valid UTF-8 CSV") from error


def import_candidates(
    db: Database,
    campaign_id: str,
    rows: list[Candidate],
    source: str,
    actor: str,
    *,
    linkedin: bool = False,
) -> CampaignRun:
    """Import a validated batch atomically; shared domains stay separate and need review."""
    if (
        source not in {"csv", "open_calgary", "openai_web_search", "google_places"}
        or not 1 <= len(rows) <= 500
    ):
        raise ValueError("Invalid import source or row count")
    with db.sessions.begin() as session:
        from app.linkedin.campaigns import active_campaign

        campaign = session.get(Campaign, campaign_id)
        if not campaign or not (
            active_campaign(session, campaign_id) if linkedin else campaign.active
        ):
            raise ValueError("Choose an active campaign")
        run = CampaignRun(
            campaign_id=campaign_id,
            source=source,
            snapshot={
                "industry": campaign.industry,
                "area": campaign.area,
                "services": campaign.services,
                "lane": campaign.lane,
                "country": campaign.country,
                "version": campaign.version,
                "daily_limit": campaign.daily_limit,
            },
            imported=0,
            duplicates=0,
        )
        session.add(run)
        session.flush()
        for row in rows:
            key = identity_key(row.name, row.city, row.address)
            source_key = row.source_key or key
            record = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.source == source, SourceRecord.source_key == source_key
                )
            )
            company = (
                session.get(Company, record.company_id)
                if record
                else session.scalar(select(Company).where(Company.identity_key == key))
            )
            if company is None:
                company = Company(
                    identity_key=key,
                    name=row.name,
                    city=row.city,
                    industry=row.industry,
                    address=row.address,
                    website=row.website,
                    phone=row.phone,
                )
                session.add(company)
                session.flush()
                run.imported += 1
            else:
                run.duplicates += 1
            if record is None:
                session.add(
                    SourceRecord(
                        source=source,
                        source_key=source_key,
                        company_id=company.id,
                        run_id=run.id,
                        source_url=row.source_url or row.website,
                    )
                )
            if row.website:
                domain = domain_of(row.website)
                matches = list(
                    session.scalars(select(CompanyDomain).where(CompanyDomain.domain == domain))
                )
                own = next((item for item in matches if item.company_id == company.id), None)
                others = [item for item in matches if item.company_id != company.id]
                if own is None:
                    own = CompanyDomain(company_id=company.id, domain=domain)
                    session.add(own)
                if others:
                    for match in [own, *others]:
                        match.status = "shared_review"
                        match.reviewed_at = None
                        match.reviewed_by = None
                if not company.website:
                    company.website = row.website
            if (
                row.email
                and session.scalar(
                    select(Contact).where(
                        Contact.company_id == company.id, Contact.email == row.email
                    )
                )
                is None
            ):
                session.add(Contact(company_id=company.id, email=row.email))
            if (
                session.scalar(
                    select(CampaignLead).where(
                        CampaignLead.campaign_id == campaign_id,
                        CampaignLead.company_id == company.id,
                    )
                )
                is None
            ):
                session.add(CampaignLead(campaign_id=campaign_id, company_id=company.id))
            session.flush()
        session.add(
            AuditEvent(
                actor_id=actor,
                action="source.import",
                entity_id=run.id,
                details={"source": source, "imported": run.imported, "duplicates": run.duplicates},
                created_at=utc_now(),
            )
        )
    from app.sources.enrichment.flow import plan_import

    plan_import(db, run.id, campaign_id, actor)
    return run
