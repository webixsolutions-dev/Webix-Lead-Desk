"""Small pilot gates, owner decisions and truthful dashboard progress."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.lead_models import CampaignLead, Company, CompanyDomain, Contact, ContactBasis
from app.db.models import WorkerInstance
from app.db.pilot_models import Pilot, PilotReservation
from app.db.research_models import Evidence
from app.db.session import Database
from app.jobs.queue import acquire_worker, finish_job
from app.pilot.readiness import metrics, readiness
from app.pilot.service import cohort, gate, save_member, transition
from app.sending.service import dispatch
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def add_candidates(db: Database, setup: dict[str, str]) -> list[dict[str, str]]:
    """Build ten complete synthetic source-reviewed candidates across the pilot sectors."""
    candidates = [dict(setup)]
    for index in range(1, 10):
        with db.sessions.begin() as session:
            company = Company(
                identity_key=f"synthetic-{index}",
                name=f"Synthetic Business {index}",
                city="Calgary",
                industry="Contractor",
                website=f"https://example{index}.com",
            )
            session.add(company)
            session.flush()
            contact = Contact(
                company_id=company.id,
                email=f"service@example{index}.com",
                verification="verified",
                verified_at=utc_now(),
                dns_checked_at=utc_now(),
                allow_role=True,
                dns_result="mx",
                dns_address=f"service@example{index}.com",
            )
            evidence = Evidence(
                company_id=company.id,
                kind="business",
                source_url=company.website,
                facts={"note": "Synthetic estimate workflow observation"},
                reviewed_by=setup["actor_id"],
            )
            session.add_all(
                [
                    contact,
                    evidence,
                    CompanyDomain(
                        company_id=company.id,
                        domain=f"example{index}.com",
                        status="verified",
                        reviewed_by=setup["actor_id"],
                    ),
                    CampaignLead(company_id=company.id, campaign_id=setup["campaign_id"]),
                ]
            )
            session.flush()
            session.add(
                ContactBasis(
                    contact_id=contact.id,
                    kind="publication",
                    source_url=company.website,
                    relevance="Synthetic business-role permission review",
                    reviewed_by=setup["actor_id"],
                    observed_at=utc_now(),
                    expires_at=utc_now() + timedelta(days=30),
                )
            )
            candidates.append(
                {
                    **setup,
                    "company_id": company.id,
                    "contact_id": contact.id,
                    "evidence_id": evidence.id,
                }
            )
    for index, candidate in enumerate(candidates):
        sector = "HVAC" if index < 4 else "Roofing" if index < 8 else "Cleaning"
        save_member(
            db,
            candidate["company_id"],
            sector,
            "Reviewed the matched website and specific estimate workflow.",
            setup["actor_id"],
        )
    return candidates


def version(db: Database) -> int:
    """Read the optimistic pilot revision used by the owner form."""
    with db.sessions() as session:
        pilot = session.get(Pilot, "primary")
        assert pilot
        return pilot.version


def test_pilot_blocks_unreviewed_cohort_and_caps_five_first_contacts(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Ten selected companies never imply ten sends, and message approvals remain separate."""
    with db.sessions.begin() as session:
        pilot = session.get(Pilot, "primary")
        assert pilot
        pilot.state = "draft"
    candidates = add_candidates(db, drafting_setup)
    assert len(cohort(db)["pilot_members"]) == 10
    transition(
        db,
        "active",
        version(db),
        "Owner reviewed ten matched businesses for the limited pilot.",
        drafting_setup["actor_id"],
    )
    for index, candidate in enumerate(candidates[:6]):
        now = NOW + timedelta(minutes=10 * index)
        item = approved_intent(db, candidate, now)
        job = claim_send(db, delivery_setup, item, now)
        transport = FakeTransport(now)

        def clock(value: datetime = now) -> datetime:
            """Freeze this test's current dispatch time."""
            return value

        dispatch(db, delivery_setup, job, transport=transport, clock=clock)
        finish_job(db, job, now, result="offline acceptance")
        assert len(transport.wires) == (1 if index < 5 else 0)
    with db.sessions() as session:
        pilot = session.get(Pilot, "primary")
        assert pilot and pilot.reserved == 5
        assert len(list(session.scalars(select(PilotReservation)))) == 5
        assert gate(session, "not-a-member", 0) == "outside_pilot_cohort"
        assert gate(session, candidates[5]["company_id"], 0) == "pilot_first_touch_limit"
        assert gate(session, candidates[0]["company_id"], 1) is None
    with pytest.raises(ValueError, match="frozen"):
        save_member(
            db,
            candidates[0]["company_id"],
            "HVAC",
            "A later unapproved cohort change",
            drafting_setup["actor_id"],
        )
    transition(
        db,
        "paused",
        version(db),
        "Pausing to inspect the first conversations and sender health.",
        drafting_setup["actor_id"],
    )
    with db.sessions() as session:
        assert gate(session, candidates[0]["company_id"], 1) == "pilot_not_active"
    transition(
        db,
        "active",
        version(db),
        "The owner reviewed the paused pilot and wishes to continue.",
        drafting_setup["actor_id"],
    )
    transition(
        db,
        "complete",
        version(db),
        "Owner reviewed accepted pilot mail and reply handling outcomes.",
        drafting_setup["actor_id"],
    )
    with db.sessions() as session:
        assert gate(session, "future-business", 0) is None


def test_pilot_owner_review_stale_version_and_no_synthetic_acceptance(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Neither a reviewer, missing cohort nor no-send checklist can count as pilot acceptance."""
    company_id, actor = drafting_setup["company_id"], drafting_setup["actor_id"]
    with db.sessions() as session:
        assert gate(session, company_id, 0) == "pilot_not_active"
    with pytest.raises(ValueError, match="Only the owner"):
        save_member(db, company_id, "HVAC", "A specific business evidence review", reviewer.user.id)
    save_member(db, company_id, "HVAC", "A specific business evidence review", actor)
    save_member(db, company_id, "HVAC", "Updated review with a clearer business observation", actor)
    with pytest.raises(ValueError, match="reload"):
        transition(db, "active", 0, "Review all real business evidence first", actor)
    with pytest.raises(ValueError, match="4 HVAC"):
        transition(db, "active", version(db), "Review all real business evidence first", actor)
    with db.sessions.begin() as session:
        pilot = session.get(Pilot, "primary")
        assert pilot
        pilot.state = "active"
    with pytest.raises(ValueError, match="at least one accepted"):
        transition(db, "complete", version(db), "No real send acceptance has occurred yet", actor)


def test_pilot_forms_and_real_metrics_exclude_preview_cards(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """The dashboard counts persisted businesses while examples remain visibly separate."""
    sign_in(client, owner)
    page = client.get("/pilot")
    assert page.status_code == 200 and "Pending" in page.text
    csrf = csrf_from(page.text)
    assert (
        client.post(
            "/pilot/members",
            data={
                "csrf_token": csrf,
                "company_id": drafting_setup["company_id"],
                "sector": "HVAC",
                "note": "Reviewed source website and heating estimate workflow.",
            },
        ).status_code
        == 303
    )
    assert "Example Heating" in client.get("/pilot").text
    assert (
        client.post(
            "/pilot/review",
            data={
                "csrf_token": csrf,
                "target": "active",
                "version": str(version(db)),
                "note": "Owner reviewed the complete cohort",
            },
        ).status_code
        == 422
    )
    assert metrics(db)["company_count"] == 1 and metrics(db)["sent_today"] == 0
    dashboard = client.get("/")
    assert dashboard.status_code == 200 and "<strong>1</strong> Businesses" in dashboard.text
    assert "Preview examples are fictional" in dashboard.text


def test_readiness_reports_worker_switch_without_claiming_provider_acceptance(
    db: Database, settings: Settings
) -> None:
    """A live process heartbeat is distinct from actual mailbox and sender readiness."""
    checks = {item["label"]: item for item in readiness(db, settings)}
    assert checks["Workspace database"]["ready"]
    assert not checks["Single worker"]["ready"] and not checks["Titan connection"]["ready"]
    assert acquire_worker(db, settings, "offline-pilot-worker", utc_now())
    checks = {item["label"]: item for item in readiness(db, settings)}
    assert checks["Single worker"]["ready"] and not checks["Worker delivery switch"]["ready"]
    with db.sessions() as session:
        worker = session.get(WorkerInstance, "primary")
        assert worker and worker.runtime_config == {
            "app_mode": "PC",
            "live_send_enabled": False,
            "mailbox_tests": False,
            "email_signatures": 1,
        }


def test_draft_cohort_removal_and_paused_review_refresh(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Unlaunched choices are editable; paused reviews cannot swap leads."""
    from app.pilot.service import remove_member

    actor, company = drafting_setup["actor_id"], drafting_setup["company_id"]
    save_member(db, company, "HVAC", "Reviewed the matched heating business website", actor)
    remove_member(db, company, actor)
    assert cohort(db)["pilot_members"] == []
    save_member(db, company, "HVAC", "Selected again after checking the website", actor)
    with db.sessions.begin() as session:
        pilot = session.get(Pilot, "primary")
        assert pilot
        pilot.state = "paused"
    save_member(db, company, "HVAC", "Fresh evidence review while the pilot is paused", actor)
    with pytest.raises(ValueError, match="frozen"):
        save_member(db, company, "Roofing", "Cannot change the sector of an active cohort", actor)
    with pytest.raises(ValueError, match="unlaunched"):
        remove_member(db, company, actor)
