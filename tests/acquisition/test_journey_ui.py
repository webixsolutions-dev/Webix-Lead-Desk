"""Immutable journal persistence and readable owner/reviewer workflow views."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.calls.listing import review_listing
from app.db.acquisition_models import LeadEvent
from app.db.session import Database
from app.journey.events import emit
from tests.acquisition.helpers import business
from tests.acquisition.test_scores_calls import listing
from tests.conftest import Account, csrf_from, sign_in


def test_events_are_idempotent_and_append_only(db: Database, owner: Account) -> None:
    """Database protections reject updates/deletes, including non-ORM writes."""
    company, _ = business(db, owner)
    for _ in range(2):
        with db.sessions.begin() as session:
            emit(session, company.id, "stable-test-key", "audited", {"reason": "Verified source"})
    with db.sessions() as session:
        events = list(
            session.scalars(select(LeadEvent).where(LeadEvent.event_key == "stable-test-key"))
        )
        assert len(events) == 1
    for statement in ("UPDATE lead_events SET kind='sent'", "DELETE FROM lead_events"):
        with pytest.raises(DatabaseError, match="append-only"), db.engine.begin() as connection:
            connection.execute(text(statement))


def test_lead_filters_detail_sources_and_owner_settings(
    client: TestClient, db: Database, owner: Account
) -> None:
    """New settings, scores, listing lane and source links are available in the real UI."""
    company, _ = business(db, owner)
    review_listing(db, company.id, listing(), owner.user.id)
    sign_in(client, owner)
    page = client.get("/leads?band=Contact%20now&website=none")
    assert page.status_code == 200 and "10/10" in page.text and company.name in page.text
    assert company.name not in client.get("/leads?band=Skip").text
    assert company.name not in client.get("/leads?stage=Won").text
    detail = client.get(f"/leads/{company.id}")
    assert (
        detail.status_code == 200
        and "Activity timeline" in detail.text
        and "Pakistan" in detail.text
    )
    assert "Score over time" in detail.text and "https://example.com/listing" in detail.text
    assert "Skip for now: score below" not in detail.text
    assert "No-website leads" in client.get("/partners").text
    settings = client.get("/settings")
    assert (
        settings.status_code == 200
        and "Lead discovery" in settings.text
        and "Lead scoring rules" in settings.text
    )
    assert (
        client.post(
            "/settings/score-rules",
            data={
                "csrf_token": csrf_from(settings.text),
                "version": "0",
                "industry": "3",
                "active_ads": "2",
                "weak_mobile": "2",
                "no_website": "3",
                "response_gap": "2",
                "reviews": "1",
                "mature": "-3",
                "contact_now": "7",
                "later": "4",
            },
        ).status_code
        == 303
    )
