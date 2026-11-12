"""Budgeted call-note drafting and end-to-end worker progression with offline adapters."""

import json
from typing import Any

import pytest
from sqlalchemy import select

from app.calls.listing import review_listing
from app.calls.notes import clean_notes
from app.calls.service import record_call
from app.core.config import Settings
from app.db.acquisition_models import CallNote
from app.db.lead_models import Company
from app.db.models import Job
from app.db.outreach_models import Draft, DraftApproval, Mailbox
from app.db.research_models import Evidence
from app.db.session import Database
from app.drafting.service import approval_blocks
from app.integrations.public_http import Page
from app.jobs.runner import dispatch_job
from app.sources.enrichment.flow import enrich_company
from tests.acquisition.helpers import business, funded, response
from tests.acquisition.test_discovery import page
from tests.acquisition.test_scores_calls import listing
from tests.conftest import Account


def mailbox(db: Database) -> Mailbox:
    """Use a synthetic enabled sender identity with no actual connection credentials."""
    with db.sessions.begin() as session:
        item = Mailbox(
            email="sales@example.org",
            lane="client",
            provider="titan",
            brand="Synthetic studio",
            sender_name="Fixture Team",
            mailing_address="1 Example Street, Calgary, AB",
            contact_details="sales@example.org",
            enabled=True,
        )
        session.add(item)
        session.flush()
        return item


def test_call_notes_create_unapproved_draft_only_after_consent(
    db: Database, owner: Account, settings: Settings
) -> None:
    """A cleaned note cannot fabricate approval or weaken website/contact prerequisites."""
    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    mailbox(db)
    review_listing(db, company.id, listing(), owner.user.id)
    call = record_call(
        db,
        company.id,
        campaign.id,
        owner.user.id,
        "notes-call",
        "interested",
        "Asked to see a homepage concept. Please email it first.",
        email="hello@example.com",
        consent=True,
        consent_note="They asked for the discussed homepage concept by email to hello@example.com.",
    )
    with db.sessions() as session:
        evidence = session.scalar(select(Evidence).where(Evidence.kind == "human_call"))
        assert evidence

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "untrusted data" in payload["instructions"]
        return {
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 100},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "summary": "They requested a homepage concept by email.",
                                    "next_step": "Prepare and review the concept.",
                                    "subject": "Your homepage concept",
                                    "body": (
                                        "Thanks for speaking with us. "
                                        "You asked to review a homepage concept. "
                                        "Which service should be most prominent?"
                                    ),
                                    "evidence_ids": [evidence.id],
                                }
                            ),
                        }
                    ],
                }
            ],
        }

    assert "unapproved" in clean_notes(db, settings, call.id, "notes-ai", provider=provider)
    with db.sessions() as session:
        note, draft = session.scalar(select(CallNote)), session.scalar(select(Draft))
        assert note and draft and note.draft_id == draft.id and draft.state == "draft"
        assert not session.scalar(select(DraftApproval))
        assert "Website identity needs review" not in approval_blocks(db, draft)
        assert not any("Company outreach paused" in block for block in approval_blocks(db, draft))
    assert "already saved" in clean_notes(
        db, settings, call.id, "duplicate-notes", provider=provider
    )


def test_import_enrichment_audit_contact_score_flow(
    db: Database, owner: Account, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker advances a new phone lead through audited contacts without any live API."""
    from app.audit.service import research as real_research

    company, campaign = business(db, owner)
    settings = funded(db, owner, settings)
    with db.sessions() as session:
        advance = session.scalar(select(Job).where(Job.kind == "advance_company"))
        assert advance
    assert "enrichment queued" in dispatch_job(db, advance, settings)
    with db.sessions() as session:
        enrichment = session.scalar(select(Job).where(Job.kind == "enrich_company"))
        assert enrichment
    enrich_company(db, settings, enrichment, provider=lambda key, payload: response(), fetcher=page)
    assert "Website audit queued" in dispatch_job(db, advance, settings)
    with db.sessions() as session:
        audit = session.scalar(select(Job).where(Job.kind == "audit"))
        assert audit

    def fetch(url: str) -> Page:
        if url.endswith("robots.txt"):
            return Page(url, 200, "User-agent: *\nAllow: /", "text/plain")
        return Page(
            url,
            200,
            "<title>Synthetic Alpine HVAC</title><p>Airdrie</p>"
            '<a href="mailto:info@example.com">Email us</a>',
            "text/html",
        )

    def audit_handler(db: Database, job: Job, *, settings: Settings | None) -> str:
        return real_research(db, job, fetcher=fetch, pause=lambda seconds: None, settings=settings)

    monkeypatch.setattr("app.audit.service.research", audit_handler)
    dispatch_job(db, audit, settings)
    mailbox(db)
    assert "assessment queued" in dispatch_job(db, advance, settings)
    with db.sessions() as session:
        assert session.scalar(select(Job.id).where(Job.kind == "assess"))
        assert not session.scalar(select(DraftApproval))
        current = session.get(Company, company.id)
        assert current and current.has_website == "true"


def test_missing_ai_or_sender_produces_visible_hold(
    db: Database, owner: Account, settings: Settings
) -> None:
    """A missing website/search configuration remains a visible human review task."""
    company, _ = business(db, owner)
    review_listing(db, company.id, listing(), owner.user.id)
    with db.sessions() as session:
        job = session.scalar(select(Job).where(Job.kind == "advance_company"))
        assert job
    assert "human call list" in dispatch_job(db, job, settings)
