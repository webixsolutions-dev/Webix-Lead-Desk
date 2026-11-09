"""Owner-only write-only credentials and exact-message scheduling through dashboard forms."""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings
from app.core.vault import openai_key, read_secret, store_secret
from app.db.delivery_models import IntegrationSecret, MailboxState, SendIntent
from app.db.outreach_models import Mailbox
from app.db.session import Database
from app.drafting.service import approve
from app.inbox.service import record_reply
from app.main import create_app
from app.sending.policy import READINESS
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW, approved_intent, reply_bytes
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company


def test_credentials_readiness_scheduling_and_inbox_forms(
    db: Database, settings: Settings, drafting_setup: dict[str, str], owner: Account
) -> None:
    """Complete owner flows without a real provider connection or secret redisplay."""
    config = settings.model_copy(
        update={"encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    with TestClient(create_app(config), follow_redirects=False) as client:
        sign_in(client, owner)
        csrf = csrf_from(client.get("/settings").text)
        key = "mailbox:" + drafting_setup["mailbox_id"]
        assert (
            client.post(
                "/settings/connections",
                data={"csrf_token": csrf, "key": key, "credential": "private-synthetic-credential"},
            ).status_code
            == 303
        )
        assert "private-synthetic-credential" not in client.get("/settings").text
        fields = {
            "csrf_token": csrf,
            "daily_limit": "50",
            "hourly_limit": "10",
            "evidence": "Synthetic dated acceptance for offline tests only",
            "enabled": "true",
        }
        url = f"/settings/mailboxes/{drafting_setup['mailbox_id']}/readiness"
        assert client.post(url, data=fields).status_code == 422
        fields.update({name: "true" for name in READINESS})
        assert client.post(url, data=fields).status_code == 303
        with db.sessions() as session:
            state = session.get(MailboxState, drafting_setup["mailbox_id"])
            assert state and state.ramp_started_at
        draft = make_draft(db, drafting_setup)
        assert (
            client.post(f"/drafts/{draft.id}/schedule", data={"csrf_token": csrf}).status_code
            == 422
        )
        approve(db, draft.id, draft.digest, owner.user.id)
        assert (
            client.post(f"/drafts/{draft.id}/schedule", data={"csrf_token": csrf}).status_code
            == 303
        )
        assert "scheduled" in client.get("/outbox").text
        record_reply(
            db,
            drafting_setup["mailbox_id"],
            reply_bytes("<script>bad</script> unsubscribe"),
            "1",
            NOW,
        )
        assert "&lt;script&gt;" in client.get("/inbox").text
        assert "<script>bad" not in client.get("/inbox").text
        assert (
            client.post(
                f"/conversations/{draft.conversation_id}/stop",
                data={"csrf_token": csrf, "suppress": "true"},
            ).status_code
            == 303
        )
        fields["enabled"] = "false"
        assert client.post(url, data=fields).status_code == 303
        with db.sessions() as session:
            box = session.get(Mailbox, drafting_setup["mailbox_id"])
            assert box and not box.enabled


def test_reviewer_cannot_save_credentials_or_schedule(
    client: TestClient, db: Database, reviewer: Account, drafting_setup: dict[str, str]
) -> None:
    """Viewer access does not grant either credential changes or message dispatch."""
    item = approved_intent(db, drafting_setup)
    sign_in(client, reviewer)
    csrf = csrf_from(client.get("/").text)
    assert (
        client.post(
            "/settings/connections",
            data={"csrf_token": csrf, "key": "openai", "credential": "test"},
        ).status_code
        == 403
    )
    assert (
        client.post(f"/drafts/{item.draft_id}/schedule", data={"csrf_token": csrf}).status_code
        == 403
    )


def test_secret_encryption_rotation_failure_and_optional_api_key(
    db: Database, settings: Settings, owner: Account
) -> None:
    """The database contains ciphertext; a different key cannot silently decode it."""
    import pytest

    config = settings.model_copy(
        update={"encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    store_secret(db, config, "openai", "synthetic-provider-key", owner.user.id)
    assert openai_key(db, config) == "synthetic-provider-key"
    store_secret(db, config, "openai", "replacement-provider-key", owner.user.id)
    with db.sessions() as session:
        secret = session.get(IntegrationSecret, "openai")
        assert secret and "replacement-provider-key" not in secret.ciphertext
    bad = config.model_copy(update={"encryption_key": SecretStr(Fernet.generate_key().decode())})
    with pytest.raises(ValueError, match="cannot be decrypted"):
        read_secret(db, bad, "openai")
    with pytest.raises(ValueError):
        store_secret(db, config, "openai", "", owner.user.id)
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        store_secret(db, settings, "openai", "synthetic", owner.user.id)


def test_reconciliation_is_queued_not_executed_in_http(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """The owner can request provider evidence without blocking a Vercel request on IMAP."""
    from app.db.models import Job

    item = approved_intent(db, drafting_setup)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/settings").text)
    url = f"/delivery/{item.id}/reconcile"
    assert client.post(url, data={"csrf_token": csrf}).status_code == 422
    with db.sessions.begin() as session:
        intent = session.get(SendIntent, item.id)
        assert intent
        intent.state = "uncertain"
    assert client.post(url, data={"csrf_token": csrf}).status_code == 303
    with db.sessions() as session:
        assert session.scalar(select(Job.kind)) == "reconcile"
