"""Native-agent pairing, exact approval, privacy and interrupted-delivery regression checks."""

import secrets
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import InboxMessage, IntegrationSecret, SendIntent
from app.db.models import AuditEvent, Job, WorkspaceSetting
from app.db.outreach_models import Conversation, Draft, DraftApproval
from app.db.session import Database
from app.db.whatsapp_models import WhatsAppConnection, WhatsAppNotice
from app.operations.portable import table_rows
from app.whatsapp import transport
from app.whatsapp.batch import KEY as BATCH_KEY
from app.whatsapp.commands import handle
from app.whatsapp.notices import collect
from app.whatsapp.settings import KEY, configure, disconnect
from app.whatsapp.worker import deliver_one, dispatch, plan
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import BODY, make_draft
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def incoming(text: str, *, sender: str = "user:12345", mid: str = "") -> dict[str, Any]:
    """Build a synthetic provider text message with an independent ID."""
    return {
        "id": mid or secrets.token_hex(8),
        "from": sender,
        "type": "text",
        "text": {"body": text},
        "timestamp": str(int(utc_now().timestamp())),
    }


def reaction(
    emoji: str,
    parent: str,
    *,
    sender: str = "user:12345",
    mid: str = "",
) -> dict[str, Any]:
    """Build a synthetic reaction tied to one exact outbound provider message."""
    return {
        "id": mid or secrets.token_hex(8),
        "from": sender,
        "type": "reaction",
        "reaction": {"message_id": parent, "emoji": emoji},
        "timestamp": str(int(utc_now().timestamp())),
    }


def paired(db: Database, settings: Settings, owner: Account, details: bool = True) -> str:
    """Exercise real pairing without contacting WhatsApp."""
    settings.public_base_url = "https://example.org"
    code = configure(db, settings, owner.user.id, secrets.token_urlsafe(32), details)
    with db.sessions() as session:
        item = session.get(WhatsAppConnection, "primary")
        assert item
        generation = item.generation
    handle(db, settings, generation, incoming("LINK " + code))
    with db.sessions.begin() as session:
        item = session.get(WhatsAppConnection, "primary")
        assert item and item.recipient == "user:12345"
        # Treat the greeting as delivered so tests focus on the selected draft.
        for notice in session.scalars(select(WhatsAppNotice)):
            notice.state = "sent"
    return generation


def review_ticket(db: Database, settings: Settings, generation: str) -> WhatsAppNotice:
    """Create the actual full draft preview and mark its provider acceptance."""
    collect(db, settings, generation)
    with db.sessions.begin() as session:
        item = session.scalar(select(WhatsAppNotice).where(WhatsAppNotice.kind == "draft"))
        assert item
        item.state = "sent"
        return item


def test_pairing_private_key_rotation_and_backup(
    db: Database,
    factor_settings: Settings,
    owner: Account,
) -> None:
    """Only a fresh pairing code connects; backups cannot transfer an approval session."""
    key = secrets.token_urlsafe(32)
    code = configure(db, factor_settings, owner.user.id, key, True)
    with db.sessions() as session:
        conn = session.get(WhatsAppConnection, "primary")
        secret = session.get(IntegrationSecret, KEY)
        assert conn and secret and key not in secret.ciphertext
        generation = conn.generation
    assert read_secret(db, factor_settings, KEY) == key
    handle(db, factor_settings, generation, incoming("hello"))
    with db.sessions() as session:
        conn = session.get(WhatsAppConnection, "primary")
        assert conn and not conn.recipient
    handle(db, factor_settings, generation, incoming("LINK " + code))
    with db.engine.connect() as connection:
        rows = table_rows(connection)
        assert rows["whatsapp_connections"] == rows["whatsapp_notices"] == []
    configure(db, factor_settings, owner.user.id, key, False)
    handle(db, factor_settings, generation, incoming("LINK " + code))
    with db.sessions() as session:
        conn = session.get(WhatsAppConnection, "primary")
        assert conn and not conn.recipient and conn.generation != generation
    disconnect(db, owner.user.id)
    with pytest.raises(ValueError, match="missing"):
        read_secret(db, factor_settings, KEY)


def test_exact_approval_schedules_once(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
) -> None:
    """The same WhatsApp command cannot approve/send twice or bypass the email service."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    ticket = review_ticket(db, factor_settings, generation)
    assert BODY in ticket.body and draft.snapshot["recipient"] in ticket.body
    command = incoming("APPROVE " + ticket.code)
    handle(db, factor_settings, generation, command)
    handle(db, factor_settings, generation, command)
    handle(db, factor_settings, generation, incoming("APPROVE " + ticket.code))
    with db.sessions() as session:
        approval = session.get(DraftApproval, draft.id)
        assert approval and approval.digest == draft.digest and approval.source == "owner"
        assert session.scalar(select(func.count()).select_from(SendIntent)) == 1
        item = session.get(WhatsAppNotice, ticket.id)
        assert item and item.action == "approved"


@pytest.mark.parametrize("case", ["shown", "pending", "late", "wrong_sender", "wrong_scope"])
def test_campaign_batch_approval_is_bound_to_prior_exact_preview(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
    case: str,
) -> None:
    """A plain approval can schedule only a prior accepted preview from the active campaign."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    now = utc_now()
    due = now + timedelta(days=2)
    with db.sessions.begin() as session:
        session.add(
            WorkspaceSetting(
                key=BATCH_KEY,
                value={
                    "campaign_id": (
                        "other-campaign" if case == "wrong_scope" else draft.snapshot["campaign_id"]
                    ),
                    "starts_at": (now - timedelta(minutes=5)).isoformat(),
                    "expires_at": (now + timedelta(days=5)).isoformat(),
                    "send_not_before": due.isoformat(),
                },
            )
        )
    ticket = review_ticket(db, factor_settings, generation)
    with db.sessions.begin() as session:
        saved = session.get(WhatsAppNotice, ticket.id)
        assert saved
        saved.message_id = "wamid.batch-preview"
        if case == "pending":
            saved.state = "pending"
        else:
            session.add(
                AuditEvent(
                    action="whatsapp.notice_sent",
                    entity_id=saved.id,
                    created_at=now + timedelta(seconds=30)
                    if case == "late"
                    else now - timedelta(seconds=5),
                )
            )
    command = incoming("approve", sender="user:999" if case == "wrong_sender" else "user:12345")
    handle(db, factor_settings, generation, command)
    handle(db, factor_settings, generation, command)
    with db.sessions() as session:
        intents = list(session.scalars(select(SendIntent)))
        if case == "shown":
            from app.sending.window import next_window

            assert len(intents) == 1 and intents[0].due_at == next_window(due)
            approval = session.get(DraftApproval, draft.id)
            assert approval and approval.digest == draft.digest
        else:
            assert not intents and not session.get(DraftApproval, draft.id)


def test_reaction_approval_schedules_exact_revision_once(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
) -> None:
    """A suggested thumbs-up is bound to the exact delivered review notification."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    ticket = review_ticket(db, factor_settings, generation)
    assert "👍 Approve" in ticket.body and "👎 Reject" in ticket.body and "✏️ Open" in ticket.body
    with db.sessions.begin() as session:
        item = session.get(WhatsAppNotice, ticket.id)
        assert item
        item.message_id = "wamid.review"
    command = reaction("👍", "wamid.review")
    handle(db, factor_settings, generation, command)
    handle(db, factor_settings, generation, command)
    handle(db, factor_settings, generation, reaction("👍", "wamid.review"))
    with db.sessions() as session:
        approval = session.get(DraftApproval, draft.id)
        assert approval and approval.digest == draft.digest and approval.source == "owner"
        assert session.scalar(select(func.count()).select_from(SendIntent)) == 1
        item = session.get(WhatsAppNotice, ticket.id)
        assert item and item.action == "approved"


def test_accepted_email_generates_one_private_status_notice(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
) -> None:
    """Only SMTP acceptance creates a sent notice; private pairing hides prospect details."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner, details=False)
    key = "email-accepted:synthetic-send"
    with db.sessions.begin() as session:
        session.add(
            SendIntent(
                id="synthetic-send",
                conversation_id=draft.conversation_id,
                draft_id=draft.id,
                mailbox_id=draft.snapshot["mailbox_id"],
                step=draft.step,
                due_at=utc_now(),
                message_id="<synthetic@example.org>",
                approval_digest=draft.digest,
            )
        )
    collect(db, factor_settings, generation)
    with db.sessions.begin() as session:
        assert not session.scalar(select(WhatsAppNotice).where(WhatsAppNotice.source_key == key))
        intent = session.get(SendIntent, "synthetic-send")
        assert intent
        intent.state, intent.accepted_at = "accepted", utc_now()
    collect(db, factor_settings, generation)
    collect(db, factor_settings, generation)
    with db.sessions() as session:
        item = session.scalars(select(WhatsAppNotice).where(WhatsAppNotice.source_key == key)).one()
        assert item.kind == "status" and not item.draft_id
        assert "accepted by the sending mail server" in item.body
        assert draft.snapshot["recipient"] not in item.body and "Subject:" not in item.body


@pytest.mark.parametrize("case", ["wrong_sender", "wrong_message", "unsupported", "summary"])
def test_reaction_shortcuts_cannot_escape_review_binding(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
    case: str,
) -> None:
    """Only a documented reaction from the paired owner on an actionable notice authorizes."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner, details=case != "summary")
    ticket = review_ticket(db, factor_settings, generation)
    with db.sessions.begin() as session:
        item = session.get(WhatsAppNotice, ticket.id)
        assert item
        item.message_id = "wamid.review"
    command = reaction("👍", "wamid.review")
    if case == "wrong_sender":
        command["from"] = "user:9876"
    elif case == "wrong_message":
        command["reaction"]["message_id"] = "wamid.other"
    elif case == "unsupported":
        command["reaction"]["emoji"] = "❤️"
    handle(db, factor_settings, generation, command)
    with db.sessions() as session:
        assert not session.get(DraftApproval, draft.id)
        assert session.scalar(select(func.count()).select_from(SendIntent)) == 0


def test_reaction_edit_and_reject_paths(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
) -> None:
    """Pencil opens review without approval; thumbs-down rejects the unsent revision."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    ticket = review_ticket(db, factor_settings, generation)
    with db.sessions.begin() as session:
        item = session.get(WhatsAppNotice, ticket.id)
        assert item
        item.message_id = "wamid.review"
    handle(db, factor_settings, generation, reaction("✏️", "wamid.review"))
    with db.sessions() as session:
        draft_item = session.get(Draft, draft.id)
        assert draft_item and draft_item.state == "draft"
        assert not session.get(DraftApproval, draft.id)
        assert any("Edit or rewrite" in n.body for n in session.scalars(select(WhatsAppNotice)))
    handle(db, factor_settings, generation, reaction("👎", "wamid.review"))
    with db.sessions() as session:
        draft_item = session.get(Draft, draft.id)
        assert draft_item and draft_item.state == "rejected"


@pytest.mark.parametrize(
    "case",
    [
        "wrong_sender",
        "reaction",
        "expired",
        "edited",
        "reply",
        "summary",
        "disconnected",
        "ambiguous",
    ],
)
def test_approval_cannot_bypass_current_state(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
    case: str,
) -> None:
    """Every stale, ambiguous, unpaired or non-reviewed action leaves mail unapproved."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner, details=case != "summary")
    ticket = review_ticket(db, factor_settings, generation)
    command = incoming("APPROVE " + ticket.code)
    if case == "wrong_sender":
        command["from"] = "user:9876"
    elif case == "reaction":
        command["type"] = "reaction"
    elif case == "expired":
        with db.sessions.begin() as session:
            item = session.get(WhatsAppNotice, ticket.id)
            assert item
            item.expires_at = utc_now() - timedelta(seconds=1)
    elif case == "edited":
        make_draft(db, drafting_setup, previous_id=draft.id, body=BODY + " Thank you.")
    elif case == "reply":
        with db.sessions.begin() as session:
            conversation = session.get(Conversation, draft.conversation_id)
            assert conversation
            conversation.state = "replied"
    elif case == "disconnected":
        disconnect(db, owner.user.id)
    elif case == "ambiguous":
        command["text"]["body"] = "yes approve all"
    handle(db, factor_settings, generation, command)
    with db.sessions() as session:
        assert not session.get(DraftApproval, draft.id)
        assert session.scalar(select(func.count()).select_from(SendIntent)) == 0


def test_edit_then_reject(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
) -> None:
    """Edit returns a link without approving; reject cancels the unsent revision."""
    draft = make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    ticket = review_ticket(db, factor_settings, generation)
    handle(db, factor_settings, generation, incoming("EDIT " + ticket.code))
    with db.sessions() as session:
        item = session.get(Draft, draft.id)
        assert item and item.state == "draft"
        assert any("Edit or rewrite" in n.body for n in session.scalars(select(WhatsAppNotice)))
    handle(db, factor_settings, generation, incoming("REJECT " + ticket.code))
    with db.sessions() as session:
        item = session.get(Draft, draft.id)
        assert item and item.state == "rejected"


def test_poll_deduplicates_replies_and_paces_sends(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One poll sends one notification; new replies are alerted once with bounded context."""
    make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    with db.sessions.begin() as session:
        session.add(
            InboxMessage(
                mailbox_id=drafting_setup["mailbox_id"],
                provider_key="test",
                dedupe_key="synthetic",
                sender="owner@example.com",
                subject="A real reply",
                body="Please share your portfolio.",
            )
        )
    sent: list[str] = []
    monkeypatch.setattr(transport, "updates", lambda *args: (10, []))

    def fake_send(key: str, to: str, body: str) -> str:
        sent.append(body)
        return "wamid.1"

    monkeypatch.setattr(transport, "send", fake_send)
    plan(db, factor_settings, utc_now())
    plan(db, factor_settings, utc_now())
    assert "checked" in dispatch(db, factor_settings, generation)
    assert len(sent) == 1 and "portfolio" in sent[0]
    dispatch(db, factor_settings, generation)
    assert len(sent) == 2 and "APPROVE" in sent[1]
    with db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(WhatsAppNotice)
                .where(WhatsAppNotice.kind == "reply")
            )
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.kind == "whatsapp")) == 1
        )
        conn = session.get(WhatsAppConnection, "primary")
        assert conn and conn.offset == 10


def test_uncertain_send_is_not_retried(
    db: Database,
    factor_settings: Settings,
    owner: Account,
    drafting_setup: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider timeout reserves the notification instead of risking a duplicate."""
    make_draft(db, drafting_setup)
    generation = paired(db, factor_settings, owner)
    collect(db, factor_settings, generation)
    calls: list[int] = []

    def fail(*args: Any) -> str:
        calls.append(1)
        raise transport.AgentError("connection_unknown")

    monkeypatch.setattr(transport, "send", fail)
    with pytest.raises(transport.AgentError):
        deliver_one(db, "synthetic", generation)
    deliver_one(db, "synthetic", generation)
    assert calls == [1]
    with db.sessions() as session:
        notice = session.scalar(select(WhatsAppNotice).where(WhatsAppNotice.kind == "draft"))
        assert notice and notice.state == "uncertain"


def test_owner_ui_and_csrf(
    client: TestClient,
    db: Database,
    factor_settings: Settings,
    owner: Account,
    reviewer: Account,
) -> None:
    """Key settings require an owner session/CSRF and never reflect the secret."""
    assert client.get("/settings/whatsapp").status_code == 303
    sign_in(client, reviewer)
    assert client.get("/settings/whatsapp").status_code == 403
    sign_in(client, owner)
    page = client.get("/settings/whatsapp")
    key = secrets.token_urlsafe(32)
    assert client.post("/settings/whatsapp", data={"api_key": key}).status_code == 403
    response = client.post(
        "/settings/whatsapp",
        data={
            "csrf_token": csrf_from(page.text),
            "api_key": key,
            "confirmed": "true",
            "share_details": "true",
        },
    )
    assert response.status_code == 200 and "LINK " in response.text
    assert key not in response.text and key not in client.get("/settings/whatsapp").text
    assert (
        client.post(
            "/settings/whatsapp/disconnect", data={"csrf_token": csrf_from(response.text)}
        ).status_code
        == 303
    )


def test_transport_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse the official nesting and reject malformed offsets without provider traffic."""
    calls: list[tuple[str, Any]] = []

    def fake(token: str, path: str, payload: Any = None) -> dict[str, Any]:
        calls.append((path, payload))
        if path.startswith("/updates"):
            return {
                "next_offset": 4,
                "entry": [{"changes": [{"value": {"messages": [incoming("hello")]}}]}],
            }
        return {"messages": [{"id": "wamid.fake"}]}

    monkeypatch.setattr(transport, "request", fake)
    offset, messages = transport.updates("synthetic", 0)
    assert offset == 4 and messages[0]["text"]["body"] == "hello"
    assert transport.send("synthetic", "user:12345", "hello") == "wamid.fake"
    assert calls[1][1]["text"]["preview_url"] is False
    with pytest.raises(transport.AgentError):
        transport.send("synthetic", "https://localhost", "hello")
    with pytest.raises(transport.AgentError):
        transport.updates("synthetic", 5)


def test_interrupted_poll_is_recoverable(
    db: Database,
    factor_settings: Settings,
    owner: Account,
) -> None:
    """A crashed polling job cannot permanently block future WhatsApp notifications."""
    from app.jobs.queue import acquire_worker, claim_next

    generation = paired(db, factor_settings, owner)
    now = utc_now()
    plan(db, factor_settings, now)
    assert acquire_worker(db, factor_settings, "test-worker", now)
    job = claim_next(db, factor_settings, "test-worker", now)
    assert job and job.kind == "whatsapp" and job.payload["generation"] == generation
    later = now + timedelta(seconds=factor_settings.job_lease_seconds + 1)
    assert acquire_worker(db, factor_settings, "replacement-worker", later)
    recovered = claim_next(db, factor_settings, "replacement-worker", later)
    assert recovered and recovered.id == job.id and recovered.claim_token != job.claim_token
