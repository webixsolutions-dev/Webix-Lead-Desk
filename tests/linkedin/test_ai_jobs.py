"""Budgeted suggestions and durable priority without live provider access."""

import json
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.linkedin_draft_models import LinkedInDraft
from app.db.models import Job
from app.db.research_models import AICall
from app.db.session import Database
from app.jobs.queue import acquire_worker, claim_next, enqueue
from app.jobs.runner import dispatch_job
from app.linkedin.ai import generate
from tests.acquisition.helpers import funded
from tests.conftest import Account
from tests.linkedin.test_drafts_actions import invitation
from tests.linkedin.test_planner import NOW


def test_ai_suggestion_is_a_new_unapproved_budgeted_revision(
    db: Database, owner: Account, settings: Settings
) -> None:
    """The fake model can suggest text but cannot preserve approval or change the recipient."""
    _, _, draft = invitation(db, owner)
    config = funded(db, owner, settings)

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["store"] is False
        assert "untrusted data" in payload["instructions"]
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "body": (
                                        "Hi Alex, I would be glad to connect "
                                        "with another Calgary business owner."
                                    ),
                                    "evidence_ids": [],
                                }
                            ),
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    assert "unapproved" in generate(db, config, draft.id, "synthetic-ai", provider=provider)
    with db.sessions() as session:
        revisions = list(session.scalars(select(LinkedInDraft).order_by(LinkedInDraft.revision)))
        assert (
            len(revisions) == 2
            and revisions[0].state == "superseded"
            and revisions[1].state == "draft"
        )
        call = session.scalar(select(AICall))
        assert call and call.category == "linkedin" and call.state == "complete"


def test_linkedin_jobs_dispatch_and_expired_generation_is_not_retried(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paid generation is held after a lost lease; accepted work takes priority over research."""
    audit = enqueue(db, "slow-research", "audit", {}, NOW)
    job = enqueue(db, "reply-suggestion", "linkedin_draft", {"draft_id": "synthetic"}, NOW)
    assert acquire_worker(db, settings, "worker", NOW)
    claimed = claim_next(db, settings, "worker", NOW)
    assert claimed and claimed.id == job.id
    monkeypatch.setattr("app.linkedin.ai.generate", lambda *args: "Unapproved suggestion")
    assert dispatch_job(db, claimed, settings) == "Unapproved suggestion"
    with db.sessions.begin() as session:
        record = session.get(Job, job.id)
        assert record
        record.lease_expires_at = NOW - timedelta(seconds=1)
    next_job = claim_next(db, settings, "worker", NOW)
    assert next_job and next_job.id == audit.id
    with db.sessions() as session:
        record = session.get(Job, job.id)
        assert record and record.state == "failed"
