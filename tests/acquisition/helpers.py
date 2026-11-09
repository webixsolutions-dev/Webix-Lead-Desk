"""Reusable synthetic acquisition fixtures, never real leads or credentials."""

from datetime import timedelta
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.lead_models import Campaign, Company
from app.db.research_models import BudgetAccount
from app.db.session import Database
from app.sources.enrichment.config import SearchConfig, save_config
from app.sources.enrichment.schema import BusinessResult, Citation, SearchResults
from app.sources.importing import import_candidates
from app.sources.validation import Candidate
from tests.conftest import Account


def business(db: Database, owner: Account, *, city: str = "Airdrie") -> tuple[Company, Campaign]:
    """Create a phone-only business and campaign via the real importer."""
    with db.sessions.begin() as session:
        campaign = Campaign(
            name="Synthetic HVAC",
            industry="HVAC",
            area=city,
            services=["Website redesign", "AI automation"],
        )
        session.add(campaign)
        session.flush()
    import_candidates(
        db,
        campaign.id,
        [
            Candidate(
                name="Synthetic Alpine HVAC",
                city=city,
                address="10 Test Street",
                industry="HVAC",
                phone="+1 403 555 0100",
            )
        ],
        "csv",
        owner.user.id,
    )
    with db.sessions() as session:
        company = session.scalar(select(Company))
        assert company
        return company, campaign


def funded(db: Database, owner: Account, settings: Settings, *, cap: int = 60) -> Settings:
    """Configure test-only API funding and rates without invoking any provider."""
    save_config(
        db,
        SearchConfig(
            daily_calls=cap, monthly_calls=100, monthly_usd_micro=1_000_000, search_usd_micro=10_000
        ),
        0,
        owner.user.id,
    )
    with db.sessions.begin() as session:
        session.add(
            BudgetAccount(
                key="primary",
                allowance=10_000_000,
                committed=0,
                expires_at=utc_now() + timedelta(days=30),
                verified_by=owner.user.id,
            )
        )
    return settings.model_copy(
        update={
            "openai_enabled": True,
            "openai_model": "test-model",
            "openai_audit_model": "",
            "openai_draft_model": "",
            "openai_api_key": SecretStr("offline-fixture"),
            "openai_input_usd_micro_per_million": 100_000,
            "openai_output_usd_micro_per_million": 200_000,
        }
    )


def result(**changes: Any) -> BusinessResult:
    """Return a fully cited fictional business on the reserved example.com domain."""
    data = {
        "name": "Synthetic Alpine HVAC",
        "city": "Airdrie",
        "address": "10 Test Street",
        "website": "https://example.com/",
        "phone": "+1 403 555 0100",
        "google_maps_url": "",
        "facebook_url": "",
        "instagram_url": "",
        "has_website": "true",
        "rating": None,
        "review_count": None,
        "place_id": "",
        "sources": [
            Citation(field=field, url="https://example.com/")
            for field in ("name", "city", "address", "website", "phone", "has_website")
        ],
    }
    data.update(changes)
    return BusinessResult.model_validate(data)


def response(item: BusinessResult | None = None) -> dict[str, Any]:
    """Produce the documented Responses web-search shape for offline tests."""
    item = item or result()
    return {
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 100},
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"sources": [{"url": "https://example.com/"}]},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": SearchResults(businesses=[item]).model_dump_json(),
                    }
                ],
            },
        ],
    }
