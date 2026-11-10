"""Evidence-dependent lead outcomes and internal planning bundles."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.classify import Recommendation, validate_recommendation
from app.audit.outcomes import may_draft
from app.core.clock import utc_now
from app.db.outreach_models import Offer
from app.db.research_models import Assessment, Evidence
from app.db.session import Database
from app.drafting.brief import build_brief
from app.playbook.bundles import install_templates, matching_bundles
from app.playbook.catalog import offer_values, service_names
from app.reporting.lead_actions import next_actions
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def result(decision: str, **changes: object) -> Recommendation:
    """Construct a minimal non-contact assessment for validation tests."""
    values = dict(
        decision=decision,
        service="",
        score=5,
        reason="The public evidence does not establish a service need.",
        observed=[],
        needs_confirmation=[],
        owner_question="What needs to be checked before making contact?",
    )
    return Recommendation.model_validate({**values, **changes})


def test_non_contact_outcomes_have_concrete_actions() -> None:
    """Old abstentions still load; research needs named checks and revisits need future dates."""
    validate_recommendation(result("abstain"), [], [])
    validate_recommendation(result("no_clear_opportunity"), [], [])
    with pytest.raises(ValueError, match="specific"):
        validate_recommendation(result("research_more"), [], [])
    validate_recommendation(
        result(
            "research_more", research_checks=["Check the official booking page for opening hours."]
        ),
        [],
        [],
    )
    with pytest.raises(ValueError, match="future"):
        validate_recommendation(result("revisit_later", revisit_on="2020-01-01"), [], [])
    validate_recommendation(
        result("revisit_later", revisit_on=(utc_now().date() + timedelta(days=5)).isoformat()),
        [],
        [],
    )
    with pytest.raises(ValueError, match="non-contact"):
        validate_recommendation(result("no_clear_opportunity", service="Website"), [], ["Website"])


def test_discovery_never_becomes_a_delivery_recommendation() -> None:
    """One observed fact can support a discovery question without proving a need."""
    fact = Evidence(
        id="observed",
        company_id="company",
        kind="business",
        source_url="https://example.test/",
        facts={},
    )
    data = result(
        "ask_first",
        service="AI voice agents",
        observed=[{"evidence_id": "observed", "observation": "The site lists booking by phone."}],
        needs_confirmation=["Ask who handles incoming bookings."],
    )
    validate_recommendation(
        data, [fact], ["AI voice agents"], discovery_services=["AI voice agents"]
    )
    with pytest.raises(ValueError, match="Discovery-only"):
        validate_recommendation(
            data.model_copy(update={"decision": "recommend"}),
            [fact],
            ["AI voice agents"],
            discovery_services=["AI voice agents"],
        )
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_recommendation(data, [], ["AI voice agents"])


def test_catalog_statuses_keep_legacy_workers_safe() -> None:
    """Only available individual services retain the legacy active flag."""
    base = {
        "service": "CRM",
        "description": "A scoped pipeline with handover.",
        "status": "discovery_only",
    }
    assert not offer_values(base)["active"]
    assert offer_values({**base, "status": "available"})["active"]
    bundle = offer_values(
        {
            **base,
            "status": "available",
            "offer_type": "bundle",
            "components": "CRM, Websites and redesign",
        }
    )
    assert not bundle["active"] and len(bundle["components"]) == 2
    with pytest.raises(ValueError, match="two to six"):
        offer_values({**base, "offer_type": "bundle"})
    with pytest.raises(ValueError, match="supported"):
        offer_values({**base, "status": "unknown"})


def test_templates_are_idempotent_internal_and_match_an_assessed_need(
    db: Database, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """Available bundles stay out of campaign choices and first-touch briefs."""
    s = drafting_setup
    with db.sessions.begin() as session:
        assert install_templates(session, owner.user.id) == 5
        assert install_templates(session, owner.user.id) == 0
        bundle = session.scalar(select(Offer).where(Offer.name == "Get Online"))
        assert bundle and not bundle.active and bundle.setup_cad_cents is None
        bundle.availability = "available"
        assert "Get Online" not in service_names(session)
        assert matching_bundles(session, s["company_id"], s["campaign_id"]) == []
        session.add(
            Assessment(
                company_id=s["company_id"],
                campaign_id=s["campaign_id"],
                origin="manual",
                result={
                    "decision": "ask_first",
                    "service": "Websites and redesign",
                    "reason": "A confirmed contact journey needs clarification.",
                },
            )
        )
    with db.sessions() as session:
        matches = matching_bundles(session, s["company_id"], s["campaign_id"])
        assert len(matches) == 1 and matches[0]["name"] == "Get Online"
    assert "Get Online" not in build_brief(db, s["company_id"], s["campaign_id"])


def test_owner_next_action_ui_queues_named_checks_and_due_revisits(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """Manual decisions require CSRF and concrete checks; later outcomes supersede earlier work."""
    s = drafting_setup
    sign_in(client, owner)
    csrf = csrf_from(client.get("/leads/" + s["company_id"]).text)
    data = {
        "csrf_token": csrf,
        "campaign_id": s["campaign_id"],
        "decision": "research_more",
        "reason": "Confirm official booking options before offering a workflow.",
        "research_checks": "Check the official booking page and calendar options.",
        "verified": "true",
    }
    url = "/leads/" + s["company_id"] + "/outcome"
    assert client.post(url, data={**data, "csrf_token": "wrong"}).status_code == 403
    assert client.post(url, data={**data, "research_checks": ""}).status_code == 422
    assert client.post(url, data=data).status_code == 303
    with db.sessions() as session:
        assert not may_draft(session, s["company_id"], s["campaign_id"])
        rows = next_actions(session, utc_now())
        assert len(rows) == 1 and "official booking" in rows[0]["detail"]
    future = (utc_now().date() + timedelta(days=3)).isoformat()
    assert (
        client.post(
            url,
            data={**data, "decision": "revisit_later", "research_checks": "", "revisit_on": future},
        ).status_code
        == 303
    )
    with db.sessions() as session:
        assert not next_actions(session, utc_now())
        assert len(next_actions(session, utc_now() + timedelta(days=4))) == 1
    assert (
        client.post(
            url, data={**data, "decision": "no_clear_opportunity", "research_checks": ""}
        ).status_code
        == 303
    )
    with db.sessions() as session:
        assert not next_actions(session, utc_now() + timedelta(days=4))
