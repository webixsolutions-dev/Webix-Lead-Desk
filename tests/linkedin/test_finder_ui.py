"""Owner search remains a budgeted proposal and UI writes stay authenticated."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.budget import BudgetBlocked
from app.core.config import Settings
from app.db.linkedin_models import Person, PersonCandidate
from app.db.session import Database
from app.integrations.openai import ModelUnavailable
from app.linkedin.finder import OwnerResults, OwnerSuggestion, find_owners
from tests.acquisition.helpers import business, funded
from tests.conftest import Account, csrf_from, sign_in


def owner_result() -> dict[str, Any]:
    """Return a fictional cited proposal in the documented Responses format."""
    candidate = OwnerSuggestion(
        name="Alex Example",
        role="Owner",
        linkedin_url="https://linkedin.com/in/alex-example",
        confidence=80,
        explanation="Public team listing and open-web profile appear to match",
        sources=["https://example.com/team"],
    )
    return {
        "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 100},
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "sources": [
                        {"url": "https://example.com/team"},
                        {"url": candidate.linkedin_url},
                    ]
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": OwnerResults(people=[candidate]).model_dump_json(),
                    }
                ],
            },
        ],
    }


def test_search_never_autoverifies_and_deduplicates_paid_attempt(
    db: Database, owner: Account, settings: Settings
) -> None:
    """One paid proposal is cached; saving a person still needs a real human review."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    calls: list[dict[str, Any]] = []

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture the only intended external call without using the network."""
        calls.append(payload)
        return owner_result()

    assert "1 proposed" in find_owners(
        db, settings, company.id, campaign.id, "search-owner", provider=provider
    )
    with db.sessions() as session:
        assert session.scalar(select(PersonCandidate))
        assert not session.scalar(select(Person))
    with pytest.raises(BudgetBlocked, match="90 days"):
        find_owners(db, settings, company.id, campaign.id, "another-search", provider=provider)
    assert len(calls) == 1 and calls[0]["max_tool_calls"] == 1


def test_uncited_owner_result_is_not_saved(
    db: Database, owner: Account, settings: Settings
) -> None:
    """Model-invented supporting URLs are rejected and cannot bypass human matching."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    response = owner_result()
    response["output"][0]["action"]["sources"] = []
    with pytest.raises(ModelUnavailable, match="source"):
        find_owners(
            db, settings, company.id, campaign.id, "bad-source", provider=lambda *_: response
        )
    with db.sessions() as session:
        assert not session.scalar(select(PersonCandidate))


def test_people_ui_requires_confirmation_and_owner_configuration(
    client: TestClient, db: Database, owner: Account, reviewer: Account
) -> None:
    """The portal exposes proposal review without making any third-party contact."""
    company, _ = business(db, owner)
    sign_in(client, owner)
    page = client.get(f"/leads/{company.id}/people")
    assert page.status_code == 200 and "Confirmed people" in page.text
    data = {
        "csrf_token": csrf_from(page.text),
        "name": "Alex Example",
        "role": "Owner",
        "url": "https://linkedin.com/in/alex-example",
        "source_url": "https://example.com/team",
        "note": "Checked the actual business team page",
        "presence": "active",
        "confidence": "85",
    }
    assert client.post(f"/leads/{company.id}/people", data=data).status_code == 422
    data["confirmed"] = "true"
    assert client.post(f"/leads/{company.id}/people", data=data).status_code == 303
    assert "Alex Example" in client.get(f"/leads/{company.id}/people").text
    sign_in(client, reviewer)
    assert client.get("/settings/linkedin").status_code == 403
    assert client.get(f"/leads/{company.id}/people").status_code == 403
