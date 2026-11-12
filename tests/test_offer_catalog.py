"""Reusable offers and proof selection without invented prices or case results."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.lead_models import Campaign, Company
from app.db.models import WorkspaceSetting
from app.db.outreach_models import Offer, ProofCard
from app.db.session import Database
from app.drafting.brief import build_brief
from app.jobs.queue import acquire_worker
from app.playbook.catalog import labels, offer_values
from app.sending.campaign_sender import SENDER_KEY_PREFIX
from tests.conftest import Account, csrf_from, sign_in


def test_quote_and_separate_currency_packages(
    db: Database, client: TestClient, owner: Account
) -> None:
    """An unknown quote is NULL, while each saved package keeps its own currency and scope."""
    sign_in(client, owner)
    csrf = csrf_from(client.get("/settings").text)
    base = {
        "csrf_token": csrf,
        "service": "CRM",
        "description": "Discovery and one sales pipeline.",
        "active": "true",
    }
    assert (
        client.post(
            "/settings/offers", data={**base, "name": "Discovery", "pricing_mode": "quote"}
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/settings/offers",
            data={
                **base,
                "name": "Implementation",
                "pricing_mode": "fixed",
                "currency": "USD",
                "billing_period": "yearly",
                "setup_cad": "1234.56",
                "recurring_cad": "200",
            },
        ).status_code
        == 303
    )
    with db.sessions() as session:
        offers = list(session.scalars(select(Offer).order_by(Offer.name)))
        assert len(offers) == 2
        quote, fixed = offers
        assert quote.setup_cad_cents is None and quote.recurring_cad_cents is None
        assert fixed.setup_cad_cents == 123456 and fixed.currency == "USD"
        assert fixed.billing_period == "yearly"
        fixed_id = fixed.id
    data = {
        **base,
        "offer_id": fixed_id,
        "version": "1",
        "name": "Scoped build",
        "pricing_mode": "quote",
        "currency": "USD",
        "billing_period": "monthly",
    }
    assert client.post("/settings/offers", data=data).status_code == 303
    assert client.post("/settings/offers", data=data).status_code == 422
    assert (
        client.post("/settings/offers", data={**data, "version": "2", "service": "Ads"}).status_code
        == 422
    )
    with db.sessions() as session:
        saved = session.get(Offer, fixed_id)
        assert saved and saved.setup_cad_cents is None and saved.recurring_cad_cents is None
    page = client.get("/settings?scope=global").text
    assert (
        "Custom quote" in page
        and "Scoped build" in page
        and "Other services" in client.get("/leads/campaigns").text
    )


@pytest.mark.parametrize("value", ["", "NaN", "Infinity", "-1", "1000001", "0.001"])
def test_fixed_price_rejects_missing_or_inexact_amounts(value: str) -> None:
    """A fixed quote cannot silently truncate fractions or accept special decimal values."""
    with pytest.raises(ValueError):
        offer_values(
            {
                "service": "CRM",
                "description": "A scoped CRM implementation.",
                "pricing_mode": "fixed",
                "setup_cad": value,
                "recurring_cad": "0",
            }
        )


def test_proofs_filtered_before_model_and_limit(db: Database, owner: Account) -> None:
    """Filter by service, industry and attribution before limiting proof."""
    with db.sessions.begin() as session:
        company = Company(
            identity_key="synthetic-catalog-test",
            name="Synthetic Service",
            city="Calgary",
            industry="HVAC",
        )
        campaign = Campaign(name="Pilot", industry="HVAC", area="Calgary", services=["CRM"])
        session.add_all([company, campaign])
        session.flush()
        company_id, campaign_id = company.id, campaign.id
        for index in range(6):
            session.add(
                ProofCard(
                    title=f"Other {index}",
                    fact="Excluded website-only result.",
                    source_url="https://example.org/proof",
                    approved_by=owner.user.id,
                    services=["Website"],
                    attribution="Example Agency",
                )
            )
        for fact, services, industries, attribution in [
            ("Relevant pipeline project.", ["CRM"], ["HVAC"], "Example Agency"),
            ("Unrelated restaurant project.", ["CRM"], ["Restaurants"], "Example Agency"),
            ("Unassigned legacy result.", [], [], "Example Agency"),
            ("Unclear authorship result.", ["CRM"], [], ""),
        ]:
            session.add(
                ProofCard(
                    title="Scope test",
                    fact=fact,
                    source_url="https://example.org/proof",
                    approved_by=owner.user.id,
                    services=services,
                    industries=industries,
                    attribution=attribution,
                )
            )
        session.add(
            Offer(
                service="CRM",
                name="Starter",
                description="One pipeline with handover.",
                pricing_mode="quote",
            )
        )
    brief = build_brief(db, company_id, campaign_id)
    assert (
        "Relevant pipeline project." in brief and "Example Agency" in brief and "Starter" in brief
    )
    for phrase in [
        "Excluded website",
        "Unrelated restaurant",
        "Unassigned legacy",
        "Unclear authorship",
    ]:
        assert phrase not in brief
    with db.sessions.begin() as session:
        changed = session.get(Campaign, campaign_id)
        assert changed
        changed.industry, changed.area, changed.country = "HVAC", "Austin, Texas", "US"
    assert "One pipeline with handover." in build_brief(db, company_id, campaign_id)


def test_proof_edit_requires_verification_and_current_version(
    db: Database, client: TestClient, owner: Account
) -> None:
    """Changing scope is itself reviewed, including service attribution and concurrent edits."""
    sign_in(client, owner)
    data = {
        "csrf_token": csrf_from(client.get("/settings").text),
        "title": "A scoped project",
        "fact": "An intake workflow was implemented.",
        "source_url": "https://example.org/proof",
        "services": "CRM, CRM",
        "industries": "HVAC",
        "attribution": "Example Agency",
    }
    assert client.post("/settings/proofs", data=data).status_code == 422
    assert client.post("/settings/proofs", data={**data, "verified": "true"}).status_code == 303
    with db.sessions() as session:
        proof = session.scalar(select(ProofCard))
        assert proof
        assert proof.services == ["CRM"]
        proof_id = proof.id
    edit = {
        **data,
        "proof_id": proof_id,
        "version": "1",
        "services": "Automation",
        "verified": "true",
    }
    assert client.post("/settings/proofs", data=edit).status_code == 303
    assert client.post("/settings/proofs", data=edit).status_code == 422
    assert "Automation" in client.get("/settings").text


def test_label_validation() -> None:
    """Bound labels and deduplicate case-insensitively."""
    assert labels("CRM, crm, Website") == ["CRM", "Website"]
    for value in ["x" * 121, "bad\nlabel", ",".join(str(i) for i in range(31))]:
        with pytest.raises(ValueError):
            labels(value)


def test_migration_preserves_existing_fixed_cad_and_unassigned_proof(
    db: Database, owner: Account
) -> None:
    """An existing workspace keeps its amounts and proof through the catalogue upgrade."""
    with db.sessions.begin() as session:
        session.add(
            Offer(
                service="CRM",
                description="Existing scoped delivery.",
                pricing_mode="fixed",
                setup_cad_cents=123456,
                recurring_cad_cents=9900,
            )
        )
        session.add(
            ProofCard(
                title="Existing proof",
                fact="Previously reviewed delivered work.",
                source_url="https://example.org/proof",
                approved_by=owner.user.id,
            )
        )
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "app/db/migrations"))
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0020_hosted_supabase")
        command.upgrade(config, "head")
    with db.sessions() as session:
        offer = session.scalar(select(Offer))
        proof = session.scalar(select(ProofCard))
        assert offer and proof
        assert offer.pricing_mode == "fixed" and offer.currency == "CAD"
        assert offer.setup_cad_cents == 123456 and offer.recurring_cad_cents == 9900
        assert proof.services == [] and proof.attribution == ""


def test_campaign_service_picker_preserves_multiple_choices(
    client: TestClient, owner: Account
) -> None:
    """Repeated checkboxes create a reusable multi-service campaign in the selected country."""
    from urllib.parse import urlencode

    sign_in(client, owner)
    body = urlencode(
        [
            ("csrf_token", csrf_from(client.get("/settings").text)),
            ("name", "Synthetic US pilot"),
            ("industry", "HVAC"),
            ("area", "Austin, Texas"),
            ("country", "US"),
            ("services", "CRM"),
            ("services", "Website"),
            ("services", ""),
        ]
    )
    response = client.post(
        "/campaigns", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 303
    page = client.get("/leads/campaigns").text
    assert "Austin, Texas" in page and "CRM, Website" in page


@pytest.mark.parametrize(
    ("country", "country_label", "city"),
    [("NZ", "New Zealand", "Auckland"), ("AE", "United Arab Emirates", "Dubai")],
)
def test_draft_only_campaign_can_be_saved_but_cannot_be_activated(
    client: TestClient, owner: Account, country: str, country_label: str, city: str
) -> None:
    """Draft-only markets stay editable while delivery is unavailable."""
    sign_in(client, owner)
    fields = {
        "csrf_token": csrf_from(client.get("/leads/campaigns").text),
        "name": f"{city} partner pilot",
        "industry": "IT services",
        "area": city,
        "country": country,
        "services": "CRM",
        "lane": "partner",
        "daily_limit": "3",
    }
    created = client.post("/campaigns", data=fields, follow_redirects=False)
    assert created.status_code == 303
    location = created.headers["location"]
    assert location.startswith("/leads/campaigns#campaign-")
    campaign_id = location.split("#campaign-")[1]
    page = client.get("/leads/campaigns").text
    assert f"{city} partner pilot · Webix partners · Paused draft" in page
    assert f'value="{country}" selected>{country_label} (draft only)' in page

    edit_url = f"/campaigns/{campaign_id}/edit"
    edit = {**fields, "version": "1", "followup_days": "4,10"}
    rejected = client.post(edit_url, data={**edit, "active": "true"})
    assert rejected.status_code == 422 and "must stay paused" in rejected.text
    saved = client.post(edit_url, data={**edit, "area": f"{city} Region"}, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"] == location
    assert f'value="{city} Region"' in client.get("/leads/campaigns").text


def test_campaign_sender_rule_requires_a_worker_that_can_enforce_it(
    client: TestClient, db: Database, owner: Account, settings: Settings
) -> None:
    """A campaign-specific rule cannot activate with an older worker."""
    with db.sessions.begin() as session:
        campaign = Campaign(
            name="Local HVAC pilot",
            industry="HVAC",
            area="Calgary",
            country="CA",
            services=["Website"],
            active=False,
        )
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id
        session.add(
            WorkspaceSetting(
                key=SENDER_KEY_PREFIX + campaign_id,
                value={"brand": "Example Web Studio", "domain": "example.org"},
            )
        )
    sign_in(client, owner)
    page = client.get("/leads/campaigns").text
    assert "Example Web Studio client outreach · Paused draft" in page
    fields = {
        "csrf_token": csrf_from(page),
        "name": "Local HVAC pilot",
        "industry": "HVAC",
        "area": "Calgary",
        "country": "CA",
        "services": "Website",
        "version": "1",
        "daily_limit": "2",
        "active": "true",
    }
    url = f"/campaigns/{campaign_id}/edit"
    blocked = client.post(url, data=fields)
    assert blocked.status_code == 422 and "worker v0.30.5" in blocked.text
    assert acquire_worker(db, settings, owner.user.id, utc_now())
    assert client.post(url, data=fields, follow_redirects=False).status_code == 303
