"""Discovery acceptance with synthetic records and no provider calls."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.clock import utc_now
from app.db.lead_models import (
    Campaign,
    CampaignLead,
    Company,
    CompanyDomain,
    Contact,
    ContactBasis,
    SourceRecord,
)
from app.db.models import Job
from app.db.session import Database
from app.integrations.http import FetchError
from app.jobs.queue import enqueue
from app.jobs.runner import PermanentJobError, TemporaryJobError, dispatch_job
from app.policy.contacts import is_suppressed, reserve_channel, suppress
from app.sources.calgary import discover
from app.sources.importing import import_candidates, parse_csv
from app.sources.validation import Candidate, domain_of, website_url
from tests.conftest import Account, csrf_from, sign_in


@pytest.fixture
def campaign(db: Database) -> Campaign:
    """Create a configured campaign with no real contacts."""
    with db.sessions.begin() as session:
        item = Campaign(
            name="Test HVAC", industry="HVAC", area="Calgary", services=["Website redesign"]
        )
        session.add(item)
        session.flush()
        return item


def candidates() -> list[Candidate]:
    """Return ten fictional companies on distinct example.com subdomains."""
    return [
        Candidate(
            name=f"Synthetic contractor {i}",
            city="Calgary",
            industry="HVAC",
            website=f"https://c{i}.example.com",
            email=f"team{i}@example.com",
        )
        for i in range(10)
    ]


def test_import_repeats_shared_domains_and_campaign_membership(
    db: Database, campaign: Campaign, owner: Account
) -> None:
    """Ten imports persist once; repeated runs and shared sites never merge franchises."""
    first = import_candidates(db, campaign.id, candidates(), "csv", owner.user.id)
    assert (first.imported, first.duplicates) == (10, 0)
    again = import_candidates(db, campaign.id, candidates(), "csv", owner.user.id)
    assert (again.imported, again.duplicates) == (0, 10)
    shared = Candidate(
        name="Separate branch",
        city="Airdrie",
        industry="HVAC",
        website="https://www.c0.example.com",
    )
    import_candidates(db, campaign.id, [shared], "csv", owner.user.id)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 11
        assert session.scalar(select(func.count()).select_from(CampaignLead)) == 11
        assert session.scalar(select(func.count()).select_from(SourceRecord)) == 11
        matches = session.scalars(
            select(CompanyDomain).where(CompanyDomain.domain == "c0.example.com")
        ).all()
        assert len(matches) == 2 and all(x.status == "shared_review" for x in matches)
        contact = session.scalar(select(Contact))
        assert contact is not None and contact.verification == "unverified"
        assert first.snapshot["area"] == "Calgary"


@pytest.mark.parametrize(
    "raw",
    [
        b"name,city\nX,Calgary",
        b"name,name,city,industry\nX,X,C,Y",
        b"name,city,industry\nX,C,Y,extra",
        b"name,city,industry\n",
        b"\xff",
        b"x" * 1_000_001,
        b"name,city,industry,website\nX,C,Y,http://127.0.0.1",
    ],
)
def test_bad_csv_is_rejected(raw: bytes) -> None:
    """Malformed, oversized and private-address candidates cannot enter an import."""
    with pytest.raises(ValueError):
        parse_csv(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "https://user:pass@example.com",
        "file:///etc/passwd",
        "https://example.com:8000",
        "https://host.internal",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    """Reject unsafe URL forms before DNS or network work."""
    with pytest.raises(ValueError):
        website_url(url)


def test_csv_normalization_and_global_policy(
    db: Database, campaign: Campaign, owner: Account
) -> None:
    """Normalization and global suppression work across independent campaigns/channels."""
    rows = parse_csv(
        b"name,city,industry,website,email\nSynthetic,Calgary,HVAC,WWW.EXAMPLE.COM,TEAM@EXAMPLE.COM\n"
    )
    assert rows[0].email == "team@example.com" and domain_of(rows[0].website) == "example.com"
    import_candidates(db, campaign.id, rows, "csv", owner.user.id)
    with db.sessions() as session:
        company = session.scalar(select(Company))
        assert company is not None
        company_id = company.id
    assert reserve_channel(db, company_id, "form")
    assert not reserve_channel(db, company_id, "email")
    assert reserve_channel(db, company_id, "form")
    suppress(db, "TEAM@example.com", "Owner stop")
    suppress(db, "team@example.com", "Repeated stop")
    assert is_suppressed(db, "team@example.com")
    assert not is_suppressed(db, "other@example.net")
    suppress(db, "example.net", "Domain stop")
    assert is_suppressed(db, "other@example.net")
    with pytest.raises(ValueError):
        suppress(db, "", "Missing address")


def test_calgary_query_and_record_mapping() -> None:
    """Query inputs cannot inject SQL; city records never invent email or website."""

    def fetch(path: str) -> bytes:
        """Return a synthetic city response and inspect the outgoing fixed query."""
        query = parse_qs(urlsplit(path).query)
        assert "'Licensed'" in query["$where"][0] and "%CONTRACTOR%" in query["$where"][0]
        return json.dumps(
            [
                {
                    "tradename": "Synthetic contractor",
                    "getbusid": "123",
                    "licencetypes": "CONTRACTOR",
                    "address": "Example address",
                }
            ]
        ).encode()

    rows = discover("contractor", 10, fetch=fetch)
    assert rows[0].source_key == "123" and rows[0].website == rows[0].email == ""
    with pytest.raises(ValueError):
        discover("x' OR 1=1", fetch=fetch)


def test_source_routes_and_contact_review(
    client: TestClient, db: Database, owner: Account, campaign: Campaign
) -> None:
    """Owner can import, review provenance and record a global no-solicitation stop."""
    sign_in(client, owner)
    csrf = csrf_from(client.get("/leads").text)
    response = client.post(
        "/sources/csv",
        data={
            "csrf_token": csrf,
            "campaign_id": campaign.id,
            "csv_text": "name,city,industry,website,email\nSynthetic,Calgary,HVAC,https://example.com,team@example.com",
        },
    )
    assert response.status_code == 303
    with db.sessions() as session:
        company = session.scalar(select(Company))
        contact = session.scalar(select(Contact))
        assert company is not None and contact is not None
        company_id, contact_id = company.id, contact.id
    assert "Synthetic" in client.get("/leads?q=Synthetic").text
    detail = client.get(f"/leads/{company_id}")
    assert detail.status_code == 200
    assert (
        client.post(
            f"/leads/{company_id}/website",
            data={
                "csrf_token": csrf,
                "website": "https://example.com",
                "note": "Matching business name and location observed",
            },
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"/contacts/{contact_id}/basis",
            data={
                "csrf_token": csrf,
                "source_url": "https://example.com/contact",
                "relevance": "Relevant to business operations role",
                "no_solicitation": "true",
            },
        ).status_code
        == 303
    )
    assert is_suppressed(db, "team@example.com")
    with db.sessions() as session:
        assert session.scalar(select(ContactBasis)) is not None
    assert (
        client.post(
            "/sources/calgary",
            data={"csrf_token": csrf, "campaign_id": campaign.id, "category": "CONTRACTOR"},
        ).status_code
        == 303
    )
    with db.sessions() as session:
        assert session.scalar(select(Job.kind)) == "calgary"
    assert (
        client.post(
            "/suppression",
            data={"csrf_token": csrf, "value": "another@example.com", "reason": "Owner stop"},
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/campaigns",
            data={
                "csrf_token": csrf,
                "name": "Roofing",
                "industry": "Roofing",
                "area": "Calgary",
                "services": "Website redesign",
                "lane": "client",
                "daily_limit": "10",
            },
        ).status_code
        == 303
    )
    assert (
        client.post("/sources/csv", data={"campaign_id": campaign.id, "csv_text": ""}).status_code
        == 403
    )


def test_reviewer_cannot_import(client: TestClient, reviewer: Account, campaign: Campaign) -> None:
    """Reviewers cannot upload contacts or change global targeting/policy."""
    sign_in(client, reviewer)
    csrf = csrf_from(client.get("/leads").text)
    assert (
        client.post(
            "/sources/csv", data={"csrf_token": csrf, "campaign_id": campaign.id, "csv_text": ""}
        ).status_code
        == 403
    )


def test_source_worker_records_results(
    db: Database, campaign: Campaign, owner: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queued discovery uses the import service and distinguishes temporary failures."""
    from app.sources import calgary

    job = enqueue(
        db,
        "test-import",
        "calgary",
        {"campaign_id": campaign.id, "category": "CONTRACTOR", "actor_id": owner.user.id},
        utc_now(),
    )
    monkeypatch.setattr(calgary, "discover", lambda category: candidates())
    assert "Imported 10" in dispatch_job(db, job)

    def failed(category: str) -> list[Candidate]:
        """Fake a retryable city API error without making an API call."""
        raise FetchError("Synthetic failure", temporary=True)

    monkeypatch.setattr(calgary, "discover", failed)
    with pytest.raises(TemporaryJobError):
        dispatch_job(db, job)
    job.kind = "unregistered"
    with pytest.raises(PermanentJobError):
        dispatch_job(db, job)
