"""Exact approval, stale context and paid suggestions with offline providers."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.delivery_models import InboxMessage
from app.db.models import AuditEvent
from app.db.outreach_models import Conversation, Draft
from app.db.research_models import AICall
from app.db.session import Database
from app.drafting.ai import generate
from app.drafting.service import approval_blocks, approve
from tests.acquisition.helpers import funded
from tests.conftest import Account, sign_in
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


def incoming(db: Database, setup: dict[str, str], first: Draft, key: str = "quote") -> None:
    """Record a reply from the actual recipient; no provider or external email is used."""
    with db.sessions.begin() as session:
        d = session.get(Draft, first.id)
        c = session.get(Conversation, first.conversation_id)
        assert d and c
        d.state = "sent"
        c.state = "replied"
        c.first_message_id = "<synthetic-first@example.test>"
        c.last_message_id = "<synthetic-reply@example.test>"
        session.add(
            InboxMessage(
                mailbox_id=setup["mailbox_id"],
                conversation_id=c.id,
                provider_key=key,
                dedupe_key=key,
                sender="service@example.com",
                subject=first.snapshot["subject"],
                body="What is the price for the website scope?",
            )
        )


def test_human_warning_requires_note_and_new_context_revokes_review(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """A style warning is explainable, requires a reason and never silently survives a new reply."""
    s = drafting_setup
    first = make_draft(db, s)
    incoming(db, s, first)
    reply = make_draft(
        db,
        s,
        kind="reply",
        step=1,
        body="Thank you for your reply. The website scope determines the price.",
    )
    assert reply.snapshot["conversation_checks"]["flags"]
    with pytest.raises(ValueError, match="override"):
        approve(db, reply.id, reply.digest, owner.user.id)
    sign_in(client, owner)
    page = client.get("/leads/" + s["company_id"] + "/draft?kind=reply")
    assert (
        reply.id in page.text
        and "override_note" in page.text
        and "Conversation checks" in page.text
    )
    approve(
        db,
        reply.id,
        reply.digest,
        owner.user.id,
        override_note="Reviewed this reply and accepted the tone for this conversation.",
    )
    with db.sessions() as session:
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "draft.approved", AuditEvent.entity_id == reply.id
            )
        )
        assert audit and audit.details["conversation_override"]
    incoming(db, s, first, "later")
    assert any("Conversation or offer" in b for b in approval_blocks(db, reply))


def test_failed_ai_suggestion_is_not_saved_but_confirmed_usage_is_charged(
    db: Database, settings: Settings, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """The provider is fully mocked; blocked copy cannot supersede a draft or erase its charge."""
    s = drafting_setup
    first = make_draft(db, s)
    incoming(db, s, first)
    config = funded(db, owner, settings)

    def provider(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "derived_checks" in payload["input"]
        return {
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "subject": first.snapshot["subject"],
                                    "body": (
                                        "Thank you for your reply. "
                                        "The website scope determines the price."
                                    ),
                                    "evidence_ids": [s["evidence_id"]],
                                }
                            ),
                        }
                    ],
                }
            ],
        }

    with pytest.raises(ValueError, match="Conversation checks"):
        generate(db, config, {**s, "kind": "reply", "step": 1}, "blocked-style", provider=provider)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Draft)) == 1
        charge = session.scalar(select(AICall))
        assert charge and charge.state == "complete" and charge.charged == 5
