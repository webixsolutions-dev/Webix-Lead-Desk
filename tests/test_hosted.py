"""Hosted connection guards and encrypted backup behavior without provider traffic."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.pool import NullPool, QueuePool

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.backup_models import BackupStatus
from app.db.connection import connect_with_backoff
from app.db.models import Job, WorkerInstance
from app.db.session import Database, create_database
from app.jobs.queue import enqueue_health
from app.operations.backups import export_backup, read_backup, restore_backup
from app.operations.hosted import HostedMonitor, dashboard_status
from app.operations.scheduled_backup import scheduled_backup
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_research import research_company as research_company

URL = "postgresql+psycopg://postgres.ref:synthetic@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"


def config(settings: Settings, **values: object) -> Settings:
    """Validate modified settings without loading any private environment."""
    return Settings.model_validate({**settings.model_dump(), **values})


def test_pool_options_and_transaction_guard(settings: Settings) -> None:
    """Cap persistent connections; reject unsafe pooler inputs with redacted errors."""
    for mode in ["PC", "VPS", "VERCEL"]:
        s = config(settings, database_url=URL, app_mode=mode, cookie_secure=True)
        db = create_database(s)
        if mode == "VERCEL":
            assert isinstance(db.engine.pool, NullPool)
        else:
            assert isinstance(db.engine.pool, QueuePool)
            assert db.engine.pool.size() == 3
            assert db.engine.pool._max_overflow == 0
            assert db.engine.pool._recycle == 300
        assert db.engine.pool._pre_ping
        db.engine.dispose()
    for invalid in [
        URL.replace("5432", "6543"),
        URL.replace("?sslmode=require", ""),
        URL.replace("aws-0-us-east-1.pooler.supabase.com", "db.ref.supabase.co"),
    ]:
        with pytest.raises(ValidationError) as error:
            config(settings, database_url=invalid)
        assert "synthetic" not in str(error.value)
    s = config(
        settings, database_url=URL.replace("5432", "6543"), database_prepare_statements=False
    )
    with patch("app.db.session.create_engine") as engine, patch("app.db.session.event.listen"):
        create_database(s)
        assert engine.call_args.kwargs["connect_args"]["prepare_threshold"] is None
    with pytest.raises(ValidationError, match="must stay false"):
        config(
            settings,
            database_url=URL,
            app_mode="VERCEL",
            cookie_secure=True,
            live_send_enabled=True,
            encryption_key=SecretStr(Fernet.generate_key().decode()),
        )


def test_connection_retry_is_before_statements_only() -> None:
    """Transient startup failures back off, while auth failures are not retried."""
    dialect = MagicMock()
    dialect.connect.side_effect = [psycopg.OperationalError("temporary"), object()]
    with patch("app.db.connection.time.sleep") as sleep:
        connect_with_backoff(dialect, MagicMock(), [], {})
        sleep.assert_called_once_with(0.25)
    dialect.connect.reset_mock()
    dialect.connect.side_effect = psycopg.errors.InvalidPassword("private")
    with patch("app.db.connection.time.sleep") as sleep:
        with pytest.raises(psycopg.errors.InvalidPassword):
            connect_with_backoff(dialect, MagicMock(), [], {})
        assert dialect.connect.call_count == 1
        sleep.assert_not_called()


def test_online_backup_retention_and_restore_holds(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    """Only owned automatic snapshots rotate; the live queue remains untouched."""
    s = config(
        settings,
        backup_directory=tmp_path / "backups",
        backup_key=SecretStr(Fernet.generate_key().decode()),
    )
    now = utc_now()
    job = enqueue_health(db, "online-snapshot", now)
    for week in range(10):
        scheduled_backup(db, s, now + timedelta(days=7 * week))
    paths = sorted(s.backup_directory.glob("*.wld")) if s.backup_directory else []
    assert len(paths) == 8
    with db.sessions() as session:
        status = session.get(BackupStatus, "primary")
        assert status and status.last_success_at and status.sha256 and not status.error_code
        assert session.get(Job, job.id).state == "queued"  # type: ignore[union-attr]
    tables = read_backup(s, paths[-1])
    assert tables["runtime_control"][0]["maintenance"] is True
    assert tables["jobs"][0]["state"] == "failed"
    assert tables["backup_status"] == []
    from app.db.migrate import migrate

    target_settings = config(s, database_url=f"sqlite:///{tmp_path / 'restore.sqlite'}")
    migrate(target_settings)
    target = create_database(target_settings)
    try:
        restore_backup(target, target_settings, paths[-1], offline_confirmed=True)
        with target.sessions() as session:
            assert session.scalar(select(Job.state)) == "failed"
    finally:
        target.engine.dispose()


def test_backup_failure_backoff_and_missing_key(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    """Failed writes never become a successful receipt or remove existing backups."""
    s = config(
        settings, backup_directory=tmp_path, backup_key=SecretStr(Fernet.generate_key().decode())
    )
    now = utc_now()
    with patch(
        "app.operations.backups.export_backup", side_effect=OSError("secret path")
    ) as export:
        scheduled_backup(db, s, now)
        scheduled_backup(db, s, now + timedelta(minutes=1))
        assert export.call_count == 1
    with db.sessions() as session:
        row = session.get(BackupStatus, "primary")
        assert row and row.last_success_at is None and row.error_code
    with patch("app.operations.scheduled_backup.reserve") as reserve:
        scheduled_backup(db, settings, now)
        reserve.assert_not_called()


def test_backup_due_and_warning_thresholds(
    db: Database, settings: Settings, tmp_path: Path
) -> None:
    """Weekly scheduling is durable; backup age, worker gap and catalog drift warn owners."""
    now = utc_now()
    s = config(
        settings, backup_directory=tmp_path, backup_key=SecretStr(Fernet.generate_key().decode())
    )
    scheduled_backup(db, s, now)
    with patch("app.operations.backups.export_backup") as export:
        scheduled_backup(db, s, now + timedelta(days=6))
        export.assert_not_called()
    with db.sessions.begin() as session:
        row = session.get(BackupStatus, "primary")
        assert row
        row.last_success_at = now - timedelta(days=9)
        session.add(
            WorkerInstance(
                key="primary",
                owner_id="fake",
                heartbeat_at=now - timedelta(days=5),
                lease_expires_at=now,
            )
        )
    monitor = HostedMonitor()
    with patch.object(
        HostedMonitor,
        "refresh",
        return_value={"bytes": 400_000_001, "unprotected": ["users"], "policies": []},
    ):
        result = dashboard_status(db, s, monitor, now)
    assert len(result["warnings"]) == 4
    assert any("RLS" in w for w in result["warnings"])
    assert any("5 days" in w for w in result["warnings"])
    assert any("8 days" in w for w in result["warnings"])
    assert any("400 MB" in w for w in result["warnings"])


def test_manual_backup_records_receipt(db: Database, settings: Settings, tmp_path: Path) -> None:
    """The CLI's online backup path writes an encrypted receipt without requiring downtime."""
    s = config(settings, backup_key=SecretStr(Fernet.generate_key().decode()))
    path = tmp_path / "manual.wld"
    export_backup(db, s, path, online=True)
    assert b'"tables"' not in path.read_bytes()
    with db.sessions() as session:
        assert session.get(BackupStatus, "primary").last_success_at  # type: ignore[union-attr]


def test_catalog_cache_and_owner_only_warnings(
    db: Database, settings: Settings, owner: object, reviewer: object
) -> None:
    """Cache catalog calls, and keep infrastructure diagnostics out of reviewer pages."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import Account, sign_in

    assert isinstance(owner, Account) and isinstance(reviewer, Account)
    now = utc_now()
    monitor = HostedMonitor()
    fake = MagicMock()
    fake.engine.dialect.name = "postgresql"
    with patch("app.operations.hosted.inspect_postgres", return_value={"bytes": 1}) as inspect:
        monitor.refresh(fake, settings, now)
        monitor.refresh(fake, settings, now + timedelta(seconds=59))
        assert inspect.call_count == 1
        monitor.refresh(fake, settings, now + timedelta(seconds=61))
        assert inspect.call_count == 2
    with TestClient(create_app(settings), follow_redirects=False) as client:
        sign_in(client, owner)
        assert "No successful encrypted backup" in client.get("/").text
        client.get("/logout")
    with TestClient(create_app(settings), follow_redirects=False) as client:
        sign_in(client, reviewer)
        assert "Workspace protection" not in client.get("/").text


def test_online_backup_fences_email_without_changing_live_identity(
    db: Database,
    settings: Settings,
    drafting_setup: dict[str, str],
    delivery_setup: Settings,
    tmp_path: Path,
) -> None:
    """An uncertain send remains non-retryable after restore and retains its Message-ID."""
    from app.db.delivery_models import SendIntent
    from app.sending.service import dispatch
    from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send

    s = config(delivery_setup, backup_key=SecretStr(Fernet.generate_key().decode()))
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, s, item)
    transport = FakeTransport(outcome="raise")
    dispatch(db, s, job, transport=transport, clock=lambda: NOW)
    path = tmp_path / "uncertain.wld"
    export_backup(db, s, path, online=True)
    tables = read_backup(s, path)
    restored = tables["send_intents"][0]
    assert restored["state"] == "uncertain" and restored["message_id"] == item.message_id
    with db.sessions() as session:
        live = session.get(SendIntent, item.id)
        assert live and live.message_id == item.message_id and live.state == "uncertain"
