"""Concurrent quotas, optional Places retention, and historical journey migration."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.audit.budget import BudgetBlocked
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import store_secret
from app.db.acquisition_models import LeadEvent, LeadScore, SearchRecord
from app.db.lead_models import Company
from app.db.migrate import migrate
from app.db.models import Job, new_id
from app.db.research_models import Evidence
from app.db.session import Database, create_database
from app.sources.enrichment.config import SearchConfig, get_config, save_config
from app.sources.enrichment.flow import discover, enrich_company
from app.sources.enrichment.quota import reserve_search
from app.sources.enrichment.schema import SearchResults
from tests.acquisition.helpers import business, funded, response, result
from tests.acquisition.test_discovery import page
from tests.conftest import Account


def test_concurrent_searches_consume_one_reservation(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Two requests for one company serialize before money or provider calls are spent."""
    company, campaign = business(db, owner)
    funded(db, owner, settings)
    config, _ = get_config(db)

    def attempt(index: int) -> str:
        try:
            return reserve_search(
                db,
                config,
                f"race-{index}",
                "company:" + company.id,
                company.id,
                campaign.id,
                "test",
                10_000,
                utc_now(),
            ).id
        except BudgetBlocked:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("blocked") == 1
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SearchRecord)) == 1


def test_places_is_off_then_retains_only_independently_verified_data(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Explicit opt-in uses encrypted credentials; raw Google listing metrics are not retained."""
    company, campaign = business(db, owner)
    assert get_config(db)[0].places_enabled is False
    settings = settings.model_copy(
        update={"encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    store_secret(db, settings, "google_places", "fixture-places-key", owner.user.id)
    save_config(
        db,
        SearchConfig(
            provider="google_places",
            places_enabled=True,
            places_daily_calls=5,
            places_monthly_calls=20,
            places_monthly_usd_micro=1_000_000,
            places_search_usd_micro=10_000,
        ),
        0,
        owner.user.id,
    )
    job = Job(
        id="places-job",
        unique_key="places-job",
        kind="enrich_company",
        due_at=utc_now(),
        payload={"company_id": company.id, "campaign_id": campaign.id, "actor_id": owner.user.id},
    )

    def places(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert key == "fixture-places-key" and payload["pageSize"] == 10
        return {
            "places": [
                {
                    "id": "synthetic-place",
                    "displayName": {"text": "Synthetic Alpine HVAC"},
                    "formattedAddress": "10 Test Street, Airdrie",
                    "websiteUri": "https://example.com/",
                    "nationalPhoneNumber": "+1 403 555 0100",
                    "rating": 4.9,
                    "userRatingCount": 123,
                    "googleMapsUri": "https://maps.google.com/?cid=synthetic",
                }
            ]
        }

    enrich_company(db, settings, job, places_provider=places, fetcher=page)
    with db.sessions() as session:
        record = session.scalar(select(SearchRecord))
        evidence = session.scalar(select(Evidence).where(Evidence.kind == "enrichment"))
        assert record and record.result == {
            "place_ids": ["synthetic-place"],
            "retention": "place_ids_only",
        }
        assert evidence and "candidate" not in evidence.facts and "rating" not in evidence.facts
        assert evidence.source_url == "https://example.com/"


def test_discovery_batch_stops_when_no_new_businesses(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Empty search results do not create an endless billable loop."""
    _, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    job = Job(
        id="empty-discovery",
        unique_key="empty-discovery",
        kind="discover",
        due_at=utc_now(),
        payload={"campaign_id": campaign.id, "actor_id": owner.user.id, "remaining_batches": 10},
    )

    def empty(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        value = response()
        value["output"][1]["content"][0]["text"] = SearchResults(businesses=[]).model_dump_json()
        return value

    assert "Found 0" in discover(db, settings, job, provider=empty, fetcher=page)
    with db.sessions() as session:
        assert not session.scalar(select(Job.id).where(Job.unique_key.like("discovery_batch:%")))


def test_discovery_does_not_import_another_city(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Out-of-area model results cannot silently change campaign targeting."""
    _, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    job = Job(
        id="wrong-city",
        unique_key="wrong-city",
        kind="discover",
        due_at=utc_now(),
        payload={"campaign_id": campaign.id, "actor_id": owner.user.id},
    )
    assert "Found 0" in discover(
        db, settings, job, provider=lambda key, data: response(result(city="Toronto")), fetcher=page
    )
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_old_database_backfills_found_score_and_append_only_events(settings: Settings) -> None:
    """Upgrade actual v0.11 rows rather than relying on the current ORM to create old data."""
    migrate(settings, "0012_outreach_spec")
    db = create_database(settings)
    company_id, campaign_id = new_id(), new_id()
    now = utc_now()
    try:
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, identity_key, name, city, industry, address, "
                    "website, phone, status, created_at) VALUES (:id, :key, 'Historical HVAC', "
                    "'Calgary', 'HVAC', '', '', '', 'new', :now)"
                ),
                {"id": company_id, "key": "historical-key", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO campaigns (id, name, industry, area, services, lane, country, "
                    "version, daily_limit, active, created_at) VALUES (:id, 'Historical campaign', "
                    "'HVAC', 'Calgary', '[]', 'client', 'CA', 1, 10, true, :now)"
                ),
                {"id": campaign_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO campaign_leads (id, campaign_id, company_id) "
                    "VALUES (:id, :campaign, :company)"
                ),
                {"id": new_id(), "campaign": campaign_id, "company": company_id},
            )
        migrate(settings)
        migrate(settings)
        with db.sessions() as session:
            events = list(session.scalars(select(LeadEvent)))
            assert {item.kind for item in events} == {"found", "scored"}
            score = session.scalar(select(LeadScore))
            assert score and score.points == 2
    finally:
        db.engine.dispose()
