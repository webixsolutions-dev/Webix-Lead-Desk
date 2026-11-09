"""Budget, provenance, website validation and automatic discovery acceptance."""

from typing import Any

import pytest
from sqlalchemy import func, select

from app.audit.budget import BudgetBlocked
from app.core.clock import utc_now
from app.core.config import Settings
from app.db.acquisition_models import SearchRecord
from app.db.lead_models import Company, CompanyDomain
from app.db.models import Job
from app.db.research_models import BudgetAccount
from app.db.session import Database
from app.integrations.openai import ModelUnavailable
from app.integrations.public_http import Page, PublicFetchError
from app.sources.enrichment.flow import discover, enrich_company
from app.sources.enrichment.providers import parse_openai
from app.sources.enrichment.service import search
from app.sources.enrichment.validation import validate_site
from tests.acquisition.helpers import business, funded, response, result
from tests.conftest import Account


def page(url: str) -> Page:
    """Return a matching first-party page without network access."""
    return Page(
        url,
        200,
        "<h1>Synthetic Alpine HVAC</h1><p>Airdrie · 10 Test Street · +1 403 555 0100</p>",
        "text/html",
    )


def test_enrichment_accepts_site_and_queues_audit(
    db: Database, owner: Account, settings: Settings
) -> None:
    """A cited matching official page is accepted, while sending remains untouched."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    job = Job(
        id="enrich-test",
        unique_key="enrich-test",
        kind="enrich_company",
        due_at=utc_now(),
        payload={"company_id": company.id, "campaign_id": campaign.id, "actor_id": owner.user.id},
    )

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["tools"][0]["type"] == "web_search"
        assert payload["max_tool_calls"] == 1 and payload["tool_choice"] == "required"
        return response()

    enrich_company(db, settings, job, provider=provider, fetcher=page)
    with db.sessions() as session:
        current = session.get(Company, company.id)
        assert (
            current and current.website == "https://example.com/" and current.has_website == "true"
        )
        match = session.scalar(select(CompanyDomain))
        assert match and match.status == "verified"
        assert session.scalar(select(Job.id).where(Job.unique_key.like("enriched:%")))
        account = session.get(BudgetAccount, "primary")
        assert account and account.committed == 10_030


@pytest.mark.parametrize(
    "url", ["https://yellowpages.ca/shop", "https://www.yelp.com/shop", "https://facebook.com/shop"]
)
def test_directory_never_becomes_official_site(url: str) -> None:
    """Directory and social hosts are rejected before a public fetch."""

    def forbidden(url: str) -> Page:
        raise AssertionError("Directory must not be fetched as official site")

    assert not validate_site(
        result(website=url), "Synthetic Alpine HVAC", "Airdrie", "", forbidden
    ).accepted


@pytest.mark.parametrize(
    "body, reason", [("Different HVAC Airdrie", "name"), ("Synthetic Alpine HVAC Toronto", "City")]
)
def test_wrong_identity_needs_review(body: str, reason: str) -> None:
    """A real-looking page is insufficient if business identity or location disagrees."""
    match = validate_site(
        result(),
        "Synthetic Alpine HVAC",
        "Airdrie",
        "10 Test Street",
        lambda url: Page(url, 200, body, "text/html"),
    )
    assert not match.accepted and reason in match.reason


def test_private_redirect_and_unavailable_stay_reviewable() -> None:
    """An inaccessible or cross-domain site is never accepted automatically."""
    assert not validate_site(
        result(),
        "Synthetic Alpine HVAC",
        "Airdrie",
        "",
        lambda url: Page(url, 302, "", "text/html", "http://127.0.0.1/secret"),
    ).accepted

    def fail(url: str) -> Page:
        raise PublicFetchError("Blocked")

    assert not validate_site(result(), "Synthetic Alpine HVAC", "Airdrie", "", fail).accepted


def test_cap_blocks_before_provider(db: Database, owner: Account, settings: Settings) -> None:
    """An exhausted or unset daily quota prevents any external call."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings, cap=0)

    def forbidden(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("No billable call without a quota")

    with pytest.raises(BudgetBlocked):
        search(db, settings, campaign.id, "test", "blocked", company.id, provider=forbidden)
    with db.sessions() as session:
        assert not session.scalar(select(SearchRecord.id))
        budget = session.get(BudgetAccount, "primary")
        assert budget and budget.committed == 0


def test_uncertain_result_holds_cost_and_ninety_day_cache(
    db: Database, owner: Account, settings: Settings
) -> None:
    """A timeout cannot trigger another paid search under a fresh job ID."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)

    def failed(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ModelUnavailable("Timed out")

    with pytest.raises(ModelUnavailable):
        search(db, settings, campaign.id, "test", "uncertain", company.id, provider=failed)
    with pytest.raises(BudgetBlocked, match="90 days"):
        search(db, settings, campaign.id, "test", "new-key", company.id, provider=failed)
    with db.sessions() as session:
        record = session.scalar(select(SearchRecord))
        account = session.get(BudgetAccount, "primary")
        assert (
            record
            and record.state == "uncertain"
            and account
            and account.committed == record.reserved
        )


def test_only_tool_returned_sources_are_accepted(settings: Settings) -> None:
    """A fabricated citation fails even when the JSON is schema-valid."""
    value = response()
    value["output"][0]["action"]["sources"] = []
    with pytest.raises(ModelUnavailable, match="source"):
        parse_openai(value, settings, 10000)


def test_any_city_discovery_deduplicates_identity(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Any-city targeting creates one membership, preserving duplicate business identity."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    job = Job(
        id="discovery-test",
        kind="discover",
        unique_key="discovery-test",
        due_at=utc_now(),
        payload={"campaign_id": campaign.id, "actor_id": owner.user.id},
    )

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "Airdrie" in payload["input"] and "HVAC" in payload["input"]
        return response()

    assert "Found 1" in discover(db, settings, job, provider=provider, fetcher=page)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 1
        assert session.get(Company, company.id)
