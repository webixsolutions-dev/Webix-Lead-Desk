"""Reviewed sales stages, immutable concepts and exact same-thread delivery."""

from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.pipeline import cents
from app.core.config import Settings
from app.db.pipeline_models import DemoVersion, Opportunity
from app.db.session import Database
from app.demos.service import create_concept, demo_message, publish_concept, revoke_concept
from app.drafting.service import approve
from app.inbox.service import record_reply
from app.jobs.queue import finish_job
from app.pipeline.handoff import call_due
from app.pipeline.service import update_opportunity
from app.sending.schedule import schedule
from app.sending.service import dispatch
from app.sending.window import CALGARY
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send, reply_bytes
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def card_values(actor_id: str, **overrides: Any) -> dict[str, Any]:
    """Supply a complete synthetic human-reviewed opportunity form."""
    values = dict(
        stage="Interested",
        assigned_to=actor_id,
        summary="Reviewed the heating estimate request with the business owner.",
        next_action="Arrange a short discussion of the estimate page.",
        meeting_at=None,
        meeting_url="",
        setup_cad_cents=0,
        recurring_cad_cents=0,
        outcome_note="",
    )
    values.update(overrides)
    return values


def replied_card(db: Database, config: Settings, setup: dict[str, str]) -> Opportunity:
    """Accept one offline message and ingest a synthetic reply through the real service."""
    intent = approved_intent(db, setup)
    job = claim_send(db, config, intent)
    transport = FakeTransport()
    dispatch(db, config, job, transport=transport, clock=lambda: NOW)
    finish_job(db, job, NOW, result="offline acceptance")
    assert len(transport.wires) == 1
    record_reply(
        db,
        setup["mailbox_id"],
        reply_bytes("I am interested. Please send more details."),
        "synthetic:reply",
        NOW + timedelta(minutes=1),
    )
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card and card.stage == "Replied"
        return card


def concept(db: Database, setup: dict[str, str]) -> DemoVersion:
    """Create a simple grounded concept without any AI or network calls."""
    return create_concept(
        db,
        setup["company_id"],
        "homepage",
        "A clearer heating estimate page",
        "A proposed page that helps visitors explain the work they need.",
        ["Describe your heating request", "Choose a suitable callback time"],
        [setup["evidence_id"]],
        setup["actor_id"],
    )


def ready_concept(db: Database, config: Settings, setup: dict[str, str]) -> DemoVersion:
    """Qualify a reply and approve the exact public concept independently of mail approval."""
    card = replied_card(db, config, setup)
    update_opportunity(
        db, card.id, card.version, setup["actor_id"], **card_values(setup["actor_id"])
    )
    item = concept(db, setup)
    publish_concept(
        db,
        config.model_copy(update={"public_base_url": "https://desk.example.org"}),
        item.id,
        item.digest,
        setup["actor_id"],
    )
    with db.sessions() as session:
        saved = session.get(DemoVersion, item.id)
        assert saved and saved.share_url
        return saved


def test_call_handoff_weekend_dst_and_money() -> None:
    """One business hour crosses weekends and DST without losing the Calgary wall clock."""
    due = call_due(datetime(2026, 10, 30, 16, 30, tzinfo=CALGARY))
    assert due == datetime(2026, 11, 2, 9, 30, tzinfo=CALGARY)
    historical = call_due(datetime(2025, 10, 31, 22, 30, tzinfo=UTC))
    assert historical.astimezone(UTC) == datetime(2025, 11, 3, 16, 30, tzinfo=UTC)
    assert cents("125.50") == 12550
    for invalid in ["NaN", "Infinity", "-1", "0.001", "1000001"]:
        with pytest.raises(ValueError):
            cents(invalid)


def test_reply_requires_human_qualification_and_version(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """Incoming words cannot qualify, publish or skip the reviewed stage path."""
    actor = drafting_setup["actor_id"]
    card = replied_card(db, delivery_setup, drafting_setup)
    with pytest.raises(ValueError, match="human-qualified"):
        concept(db, drafting_setup)
    with pytest.raises(ValueError, match="next stage"):
        update_opportunity(
            db,
            card.id,
            1,
            actor,
            **card_values(actor, stage="Won", outcome_note="Synthetic deal signed"),
        )
    update_opportunity(db, card.id, 1, actor, **card_values(actor))
    with pytest.raises(ValueError, match="another tab"):
        update_opportunity(db, card.id, 1, actor, **card_values(actor))
    with pytest.raises(ValueError, match="time and assigned"):
        update_opportunity(db, card.id, 2, actor, **card_values(actor, stage="Call booked"))
    item = concept(db, drafting_setup)
    assert item.state == "draft" and item.share_url is None
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        publish_concept(db, delivery_setup, item.id, item.digest, actor)


def test_concept_message_keeps_thread_and_separate_approval(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """An approved public concept is not permission to email it; acceptance advances the board."""
    item = ready_concept(db, delivery_setup, drafting_setup)
    body = (
        "Thanks for explaining your estimate process. Here is the concept we discussed: "
        f"{item.share_url}. Would this be useful?"
    )
    draft = demo_message(db, item.id, body, drafting_setup["actor_id"])
    assert draft.state == "draft" and draft.step == 2
    assert demo_message(db, item.id, body, drafting_setup["actor_id"]).id == draft.id
    with pytest.raises(ValueError):
        schedule(db, draft.id, drafting_setup["actor_id"], NOW)
    approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
    now = NOW + timedelta(minutes=10)
    intent = schedule(db, draft.id, drafting_setup["actor_id"], now)
    assert intent.due_at == now
    job = claim_send(db, delivery_setup, intent, now)
    transport = FakeTransport(now)
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: now)
    assert len(transport.wires) == 1
    message = BytesParser(policy=policy.default).parsebytes(transport.wires[0])
    assert str(message["From"]).endswith("<sales@example.org>")
    assert str(message["In-Reply-To"]) == draft.snapshot["in_reply_to"]
    assert str(message["References"]) == draft.snapshot["references"][0]
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card and card.stage == "Demo sent"


@pytest.mark.parametrize("change", ["reply", "revoke"])
def test_concept_final_guard_observes_reply_or_revocation(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], change: str
) -> None:
    """A review change in the last transport interval stops DATA and does not mark demo sent."""
    item = ready_concept(db, delivery_setup, drafting_setup)
    draft = demo_message(
        db,
        item.id,
        f"Thanks for your interest. Here is the proposed page: {item.share_url}",
        drafting_setup["actor_id"],
    )
    approve(db, draft.id, draft.digest, drafting_setup["actor_id"])
    now = NOW + timedelta(minutes=10)
    intent = schedule(db, draft.id, drafting_setup["actor_id"], now)
    transport = FakeTransport(now)

    def changed() -> None:
        """Apply a concurrent inbound event or owner decision before DATA."""
        if change == "reply":
            record_reply(
                db,
                drafting_setup["mailbox_id"],
                reply_bytes("Please wait until next week."),
                "synthetic:second",
                now,
            )
        else:
            revoke_concept(db, item.id, drafting_setup["actor_id"])

    transport.on_data = changed
    dispatch(
        db,
        delivery_setup,
        claim_send(db, delivery_setup, intent, now),
        transport=transport,
        clock=lambda: now,
    )
    assert transport.wires == []
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card and card.stage == "Interested"


def test_public_concept_is_exact_revocable_and_private_data_free(
    db: Database,
    delivery_setup: Settings,
    drafting_setup: dict[str, str],
    client: TestClient,
    owner: Account,
    reviewer: Account,
) -> None:
    """Only an approved snapshot is public, and reviewers cannot publish or prepare mail."""
    item = ready_concept(db, delivery_setup, drafting_setup)
    url = "/concepts/" + str(item.share_token)
    page = client.get(url)
    assert page.status_code == 200 and "Concept only" in page.text
    assert drafting_setup["evidence_id"] not in page.text and "service@example.com" not in page.text
    assert "noindex" in page.text and client.get("/concepts/missing").status_code == 404
    sign_in(client, reviewer)
    page = client.get("/demos/" + item.id)
    assert page.status_code == 200
    assert (
        client.post(
            f"/demos/{item.id}/message",
            data={"csrf_token": csrf_from(client.get("/pipeline").text), "body": "test"},
        ).status_code
        == 403
    )
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/pipeline").text)})
    sign_in(client, owner)
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card
    page = client.get("/pipeline/" + card.id)
    assert page.status_code == 200 and "Call handoff" in page.text
    data = {
        "csrf_token": csrf_from(page.text),
        "version": str(card.version),
        "stage": "Interested",
        "assigned_to": owner.user.id,
        "summary": "A human reviewed this specific estimate workflow.",
        "next_action": "Discuss the concept",
        "meeting_at": "",
        "meeting_url": "",
        "setup_cad": "500",
        "recurring_cad": "100",
        "outcome_note": "",
    }
    assert client.post("/pipeline/" + card.id, data=data).status_code == 303
    revoke_concept(db, item.id, owner.user.id)
    assert client.get(url).status_code == 404
    with pytest.raises(ValueError, match="approved"):
        demo_message(db, item.id, "An unapproved response", owner.user.id)
