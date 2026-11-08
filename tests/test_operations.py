"""Encrypted backup, conservative crash recovery and empty-target cutover rehearsal."""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import SendIntent
from app.db.migrate import migrate
from app.db.models import Job, User, UserSession, WorkerInstance
from app.db.operations_models import RuntimeControl
from app.db.outreach_models import Conversation, Draft, DraftApproval
from app.db.session import Database, create_database
from app.jobs.queue import acquire_worker, finish_job, release_worker
from app.operations.backups import export_backup, read_backup, require_empty_target, restore_backup
from app.operations.control import quiesce, set_maintenance
from app.policy.contacts import suppress
from app.sending.service import dispatch
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company


def backup_config(settings: Settings) -> Settings:
    """Add a fresh synthetic dedicated backup key, without changing sender configuration."""
    return settings.model_copy(update={"backup_key": SecretStr(Fernet.generate_key().decode())})


def empty_target(config: Settings, path: Path) -> tuple[Database, Settings]:
    """Create a separate empty schema with all delivery switches disabled."""
    settings = config.model_copy(
        update={"database_url": "sqlite:///" + str(path), "live_send_enabled": False}
    )
    migrate(settings)
    return create_database(settings), settings


def test_encrypted_roundtrip_preserves_exact_delivery_guards(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], tmp_path: Path
) -> None:
    """Uncertain mail, approvals, encrypted credentials and stops survive restore."""
    config = backup_config(delivery_setup)
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, config, item)
    transport = FakeTransport(outcome="raise")
    dispatch(db, config, job, transport=transport, clock=lambda: NOW)
    finish_job(db, job, NOW, result="offline unknown outcome")
    release_worker(db, "test-worker", utc_now() - timedelta(seconds=1))
    suppress(db, "service@example.com", "Synthetic stop preserved through cutover")
    with db.sessions.begin() as session:
        session.add(
            UserSession(
                token_hash="a" * 64,
                user_id=drafting_setup["actor_id"],
                csrf_token="b" * 64,
                expires_at=utc_now() + timedelta(hours=1),
            )
        )
    set_maintenance(db, True, "Preparing a synthetic offline cutover rehearsal")
    path = tmp_path / "snapshot.wld"
    info = export_backup(db, config, path, offline_confirmed=True)
    assert info["rows"] > 10 and b"service@example.com" not in path.read_bytes()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    tables = read_backup(config, path)
    assert tables["sessions"] == []
    target, target_settings = empty_target(config, tmp_path / "restored.sqlite")
    try:
        counts = restore_backup(target, target_settings, path, offline_confirmed=True)
        assert counts["send_intents"] == 1 and counts["suppression_entries"] == 1
        with target.sessions() as session:
            saved = session.get(SendIntent, item.id)
            assert saved and saved.state == "uncertain"
            assert saved.message_id == item.message_id and saved.mailbox_id == item.mailbox_id
            draft = session.get(Draft, item.draft_id)
            approval = session.get(DraftApproval, item.draft_id)
            assert draft and approval and approval.digest == draft.digest == saved.approval_digest
            conversation = session.get(Conversation, item.conversation_id)
            assert (
                conversation
                and conversation.locked_at
                and conversation.mailbox_id == item.mailbox_id
            )
            control = session.get(RuntimeControl, "primary")
            assert control and control.maintenance
            assert session.scalar(select(func.count()).select_from(UserSession)) == 0
        assert not acquire_worker(target, target_settings, "target-worker", utc_now())
        assert (
            read_secret(target, target_settings, "mailbox:" + item.mailbox_id)
            == "offline-only-password"
        )
        with pytest.raises(ValueError, match="contains data"):
            restore_backup(target, target_settings, path, offline_confirmed=True)
    finally:
        target.engine.dispose()


def test_backup_rejects_missing_guards_wrong_keys_and_corruption(
    db: Database, settings: Settings, owner: Account, tmp_path: Path
) -> None:
    """No backup is produced without an offline fence; bad files cannot modify a target."""
    config = backup_config(settings)
    path = tmp_path / "private.wld"
    with pytest.raises(ValueError, match="offline-confirmed"):
        export_backup(db, config, path)
    with pytest.raises(ValueError, match="maintenance"):
        export_backup(db, config, path, offline_confirmed=True)
    set_maintenance(db, True, "Synthetic database backup test")
    export_backup(db, config, path, offline_confirmed=True)
    with pytest.raises(ValueError, match="exists"):
        export_backup(db, config, path, offline_confirmed=True)
    with pytest.raises(ValueError, match="authenticated"):
        read_backup(backup_config(settings), path)
    bad = tmp_path / "tampered.wld"
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    bad.write_bytes(data)
    target, target_settings = empty_target(config, tmp_path / "target.sqlite")
    try:
        with pytest.raises(ValueError, match="authenticated"):
            restore_backup(target, target_settings, bad, offline_confirmed=True)
        with target.sessions() as session:
            assert session.scalar(select(func.count()).select_from(User)) == 0
        with pytest.raises(ValueError, match="disable LIVE_SEND_ENABLED"):
            restore_backup(
                target,
                target_settings.model_copy(update={"live_send_enabled": True}),
                path,
                offline_confirmed=True,
            )
        with target.engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated (id INTEGER)"))
        with pytest.raises(ValueError, match="unrelated"):
            require_empty_target(target)
    finally:
        target.engine.dispose()


def test_maintenance_blocks_writes_worker_and_last_send_guard(
    db: Database,
    client: TestClient,
    owner: Account,
    delivery_setup: Settings,
    drafting_setup: dict[str, str],
) -> None:
    """Maintenance fences dispatch immediately before DATA and makes web mutations read-only."""
    sign_in(client, owner)
    csrf = csrf_from(client.get("/operations").text)
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    transport = FakeTransport()
    transport.on_data = lambda: set_maintenance(
        db, True, "Synthetic maintenance during final dispatch guard"
    )
    dispatch(db, delivery_setup, job, transport=transport, clock=lambda: NOW)
    assert transport.wires == []
    assert not acquire_worker(db, delivery_setup, "another-worker", NOW)
    assert client.post("/campaigns", data={"csrf_token": csrf}).status_code == 503
    page = client.get("/operations")
    assert page.status_code == 200 and "new writes" in page.text
    assert "offline-only-password" not in page.text


def test_quiesce_requires_expired_leases_and_marks_interrupted_work(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str]
) -> None:
    """A vanished worker's claims are stopped without making an uncertain SMTP job retryable."""
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, delivery_setup, item)
    set_maintenance(db, True, "Recovering a synthetic interrupted worker")
    with db.sessions.begin() as session:
        worker = session.get(WorkerInstance, "primary")
        stored = session.get(Job, job.id)
        assert worker and stored
        worker.lease_expires_at = stored.lease_expires_at = utc_now() + timedelta(minutes=1)
    with pytest.raises(ValueError, match="worker lease"):
        quiesce(db, offline_confirmed=True)
    with db.sessions.begin() as session:
        worker = session.get(WorkerInstance, "primary")
        assert worker
        worker.lease_expires_at = utc_now() - timedelta(seconds=1)
    with pytest.raises(ValueError, match="job lease"):
        quiesce(db, offline_confirmed=True)
    with db.sessions.begin() as session:
        worker = session.get(WorkerInstance, "primary")
        stored = session.get(Job, job.id)
        intent = session.get(SendIntent, item.id)
        assert worker and stored and intent
        worker.lease_expires_at = utc_now() - timedelta(seconds=1)
        stored.lease_expires_at = utc_now() - timedelta(seconds=1)
        intent.state = "transmitting"
    quiesce(db, offline_confirmed=True)
    with db.sessions() as session:
        intent = session.get(SendIntent, item.id)
        stored = session.get(Job, job.id)
        assert intent and intent.state == "uncertain" and stored and stored.state == "failed"


def test_private_key_setup_preserves_existing_values(tmp_path: Path) -> None:
    """Key generation neither replaces configured credentials nor exposes them as output."""
    from scripts.add_backup_key import add_key

    path = tmp_path / "private.env"
    path.write_text("APP_MODE=PC\nBACKUP_KEY=\n")
    assert add_key(path)
    first = path.read_text()
    assert "APP_MODE=PC" in first and "BACKUP_KEY=\n" not in first
    assert not add_key(path) and path.read_text() == first


def test_cli_backup_verify_restore_and_resume(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    settings: Settings,
    owner: Account,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator commands preserve accounts, retain maintenance and never log keys."""
    from app import cli
    from tests.test_cli import invoke

    config = backup_config(settings)
    monkeypatch.setattr(cli, "Settings", lambda: config)
    path = tmp_path / "cli.wld"
    assert invoke(monkeypatch, ["maintenance", "on", "--note", "Offline CLI recovery test"]) == 0
    assert invoke(monkeypatch, ["backup", "--file", str(path)]) == 2
    assert invoke(monkeypatch, ["backup", "--file", str(path), "--offline-confirmed"]) == 0
    assert invoke(monkeypatch, ["verify-backup", "--file", str(path)]) == 0
    target, target_config = empty_target(config, tmp_path / "cli-restored.sqlite")
    try:
        monkeypatch.setattr(cli, "Settings", lambda: target_config)
        assert invoke(monkeypatch, ["restore", "--file", str(path), "--offline-confirmed"]) == 0
        with target.sessions() as session:
            assert session.get(User, owner.user.id)
            control = session.get(RuntimeControl, "primary")
            assert control and control.maintenance
        assert (
            invoke(monkeypatch, ["maintenance", "off", "--note", "Verified restored test data"])
            == 0
        )
        with target.sessions() as session:
            control = session.get(RuntimeControl, "primary")
            assert control and not control.maintenance
    finally:
        target.engine.dispose()
    assert config.backup_key and config.backup_key.get_secret_value() not in caplog.text


def test_backup_requires_original_credential_key(
    db: Database, delivery_setup: Settings, tmp_path: Path
) -> None:
    """A snapshot cannot claim recoverable credentials when the configured key is wrong."""
    config = backup_config(delivery_setup)
    set_maintenance(db, True, "Synthetic credential backup verification")
    path = tmp_path / "credentials.wld"
    wrong = config.model_copy(update={"encryption_key": SecretStr(Fernet.generate_key().decode())})
    with pytest.raises(ValueError, match="cannot open stored credentials"):
        export_backup(db, wrong, path, offline_confirmed=True)
    assert not path.exists()
    export_backup(db, config, path, offline_confirmed=True)
    with pytest.raises(ValueError, match="original ENCRYPTION_KEY"):
        read_backup(wrong, path)
