"""Trusted feedback becomes memory only after exact owner review, with reversible versioning."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.learning_models import LearningProposal
from app.db.outreach_models import Draft, PlaybookVersion
from app.db.session import Database
from app.drafting.brief import build_brief
from app.drafting.service import approve
from app.learning.service import apply_proposal, dismiss_proposal, rollback
from app.pipeline.service import update_opportunity
from app.playbook.service import current_guidance, publish
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import BODY, make_draft
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_pipeline_demos import card_values, replied_card
from tests.test_research import research_company as research_company


def edited_proposal(db: Database, setup: dict[str, str]) -> LearningProposal:
    """Edit a first-contact message through the production immutable draft flow."""
    first = make_draft(db, setup)
    make_draft(db, setup, previous_id=first.id, body=BODY + " Thank you for your time.")
    with db.sessions() as session:
        proposal = session.scalar(select(LearningProposal))
        assert proposal
        return proposal


def test_edits_are_inert_until_owner_applies_exact_rule(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """A style example stays inert until owner review."""
    proposal = edited_proposal(db, drafting_setup)
    assert current_guidance(db, drafting_setup["campaign_id"]) == []
    rule = "Prefer one specific observation and a short, low-pressure question."
    with pytest.raises(ValueError, match="Only the owner"):
        apply_proposal(db, proposal.id, rule, 0, reviewer.user.id)
    version = apply_proposal(db, proposal.id, rule, 0, drafting_setup["actor_id"])
    assert version.version == 1 and rule in build_brief(
        db, drafting_setup["company_id"], drafting_setup["campaign_id"]
    )
    with pytest.raises(ValueError, match="already reviewed"):
        apply_proposal(db, proposal.id, rule, 1, drafting_setup["actor_id"])


def test_stale_learning_review_rolls_back_both_changes(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A stale review cannot publish or consume its pending proposal."""
    proposal = edited_proposal(db, drafting_setup)
    publish(
        db,
        proposal.scope,
        "Use concise natural writing with specific observed facts.",
        0,
        drafting_setup["actor_id"],
    )
    with pytest.raises(ValueError, match="Guidance changed"):
        apply_proposal(
            db,
            proposal.id,
            "Ask only one clear question per message.",
            0,
            drafting_setup["actor_id"],
        )
    with db.sessions() as session:
        saved = session.get(LearningProposal, proposal.id)
        assert saved and saved.state == "pending"
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlaybookVersion)
                .where(PlaybookVersion.scope != "partner")
            )
            == 1
        )
    dismiss_proposal(db, proposal.id, drafting_setup["actor_id"])
    assert len(current_guidance(db, proposal.scope)) == 1


def test_concurrent_application_publishes_once(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Two owner tabs cannot append the same suggestion twice."""
    proposal = edited_proposal(db, drafting_setup)

    def apply_once(_: int) -> bool:
        """Race the same expected guidance version."""
        try:
            apply_proposal(
                db,
                proposal.id,
                "Use the revised style while keeping facts prospect-specific.",
                0,
                drafting_setup["actor_id"],
            )
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(apply_once, range(2))) == 1
    with db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlaybookVersion)
                .where(PlaybookVersion.scope != "partner")
            )
            == 1
        )


def test_rollback_appends_history_and_preserves_approved_draft(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Restoration preserves previously approved messages and their guidance references."""
    actor, scope = drafting_setup["actor_id"], drafting_setup["campaign_id"]
    original = publish(db, scope, "Use one natural, evidence-backed question.", 0, actor)
    second = publish(
        db, scope, "Keep the introductory observation to one short sentence.", 1, actor
    )
    draft = make_draft(db, drafting_setup)
    approve(db, draft.id, draft.digest, actor)
    restored = rollback(db, original.id, 2, actor)
    assert (
        restored.version == 3
        and restored.guidance == original.guidance
        and restored.id != original.id
    )
    with db.sessions() as session:
        saved = session.get(Draft, draft.id)
        assert saved and saved.state == "approved"
        assert saved.digest == draft.digest and saved.snapshot["playbook_ids"] == [second.id]
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlaybookVersion)
                .where(PlaybookVersion.scope != "partner")
            )
            == 3
        )


def test_raw_reply_does_not_write_guidance_but_human_outcome_proposes_review(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Raw replies cannot write proposals; internal outcomes still require review."""
    card = replied_card(db, delivery_setup, drafting_setup)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(LearningProposal)) == 0
    update_opportunity(
        db,
        card.id,
        card.version,
        drafting_setup["actor_id"],
        **card_values(
            drafting_setup["actor_id"],
            stage="Lost",
            outcome_note="The business chose to keep its current provider this quarter.",
        ),
    )
    with db.sessions() as session:
        proposal = session.scalar(select(LearningProposal))
        assert proposal
        assert proposal.source_kind == "outcome" and proposal.state == "pending"
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlaybookVersion)
                .where(PlaybookVersion.scope != "partner")
            )
            == 0
        )


def test_rejection_route_and_learning_confirmation(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """Rejection feedback requires a separate checked review before publication."""
    draft = make_draft(db, drafting_setup)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/learning").text)
    assert (
        client.post(
            f"/drafts/{draft.id}/reject",
            data={
                "csrf_token": csrf,
                "reason": "Make the observation more specific to this business.",
            },
        ).status_code
        == 303
    )
    page = client.get("/learning")
    assert page.status_code == 200 and "Remember why" in page.text
    with db.sessions() as session:
        proposal = session.scalar(select(LearningProposal))
        assert proposal
    payload = {
        "csrf_token": csrf,
        "action": "apply",
        "rule": "Use a specific observation from this business's evidence.",
        "version": "0",
    }
    assert client.post(f"/learning/{proposal.id}/review", data=payload).status_code == 422
    payload["reviewed"] = "true"
    assert client.post(f"/learning/{proposal.id}/review", data=payload).status_code == 303
    assert "version 1" in client.get("/learning").text
