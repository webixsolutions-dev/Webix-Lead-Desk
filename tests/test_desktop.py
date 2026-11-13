"""Desktop configuration, graceful shutdown and secret-safe errors; no live providers."""

import sys
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job, WorkerInstance
from app.db.session import Database
from app.desktop import config as storage
from app.desktop import controller
from app.desktop.config import DesktopConfig
from app.desktop.platform import protect
from app.jobs import runner
from app.jobs.queue import enqueue_health


def desktop_config(tmp_path: Path) -> DesktopConfig:
    """Generate synthetic keys, never user credentials."""
    return DesktopConfig(
        database_url=SecretStr("postgresql+psycopg://offline@localhost/test?sslmode=require"),
        encryption_key=SecretStr(Fernet.generate_key().decode()),
        backup_directory=str(tmp_path / "backups"),
    )


def test_encrypted_roundtrip_and_preserved_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use fake platform encryption offline and ensure no plaintext is written."""
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(
        storage,
        "protect",
        lambda data, decrypt=False: cipher.decrypt(data) if decrypt else cipher.encrypt(data),
    )
    config = desktop_config(tmp_path)
    storage.save_config(tmp_path, config)
    raw = (tmp_path / "settings.dat").read_bytes()
    assert config.encryption_key.get_secret_value().encode() not in raw
    assert b"postgresql" not in raw
    loaded = storage.load_config(tmp_path)
    assert loaded is not None and loaded.backup_key == config.backup_key
    loaded.dashboard_url = "https://example.com"
    storage.save_config(tmp_path, loaded)
    assert storage.load_config(tmp_path).session_secret == config.session_secret  # type: ignore[union-attr]
    before = (tmp_path / "settings.dat").read_bytes()
    monkeypatch.setattr(storage, "protect", Mock(side_effect=OSError("synthetic")))
    with pytest.raises(OSError):
        storage.save_config(tmp_path, loaded)
    assert (tmp_path / "settings.dat").read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(OSError):
        storage.load_config(tmp_path)


def test_safety_gates_ignore_ambient_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop cannot inherit a sending-enabled cloud or developer environment."""
    for key in (
        "LIVE_SEND_ENABLED",
        "OPENAI_ENABLED",
        "LINKEDIN_POSTING_ENABLED",
        "EXTERNAL_INTEGRATIONS_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    settings = desktop_config(tmp_path).settings(tmp_path)
    assert settings.app_mode == "PC"
    assert not settings.live_send_enabled and not settings.openai_enabled
    assert settings.openai_search_enabled
    assert not settings.linkedin_posting_enabled and not settings.external_integrations_enabled
    assert settings.backup_keep == 8 and settings.backup_interval_days == 7


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.sqlite",
        "postgresql+psycopg://offline@localhost/test",
        "postgresql+psycopg://offline@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require",
    ],
)
def test_invalid_connection_never_saved(tmp_path: Path, url: str) -> None:
    """Reject non-hosted, unencrypted and prepared-statement-unsafe connections."""
    config = desktop_config(tmp_path)
    config.database_url = SecretStr(url)
    with pytest.raises(ValueError):
        storage.save_config(tmp_path, config)
    assert not (tmp_path / "settings.dat").exists()


def test_stop_finishes_claim_and_does_not_take_next_job(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop during a handler preserves its completion and leaves the next job queued."""
    stop = Event()
    first = enqueue_health(db, "first-desktop", utc_now())
    second = enqueue_health(db, "second-desktop", utc_now())
    started = Mock()

    def dispatch(database: Database, job: Job, config: Settings) -> str:
        """Simulate clicking Stop while an offline job is in flight."""
        stop.set()
        return "Finished current job"

    monkeypatch.setattr(runner, "dispatch_job", dispatch)
    runner.run_worker(db, settings, stop_event=stop, on_started=started)
    started.assert_called_once()
    with db.sessions() as session:
        assert session.get(Job, first.id).state == "succeeded"  # type: ignore[union-attr]
        assert session.get(Job, second.id).state == "queued"  # type: ignore[union-attr]
        lease = session.get(WorkerInstance, "primary")
        assert lease and lease.lease_expires_at <= utc_now()


def test_controller_serializes_and_redacts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate clicks cannot start another worker, and provider errors remain private."""
    entered, release = Event(), Event()

    def check(config: Settings) -> None:
        """Wait until the test has attempted a duplicate start."""
        entered.set()
        assert release.wait(3)
        raise RuntimeError("PRIVATE_CONNECTION_SECRET")

    monkeypatch.setattr(controller, "check_connection", check)
    worker = controller.WorkerController()
    worker.start(settings)
    assert entered.wait(3)
    with pytest.raises(RuntimeError):
        worker.start(settings)
    worker.stop()
    release.set()
    assert worker.thread is not None
    worker.thread.join(3)
    assert not worker.active
    messages = []
    while not worker.messages.empty():
        messages.append(worker.messages.get_nowait())
    assert "PRIVATE_CONNECTION_SECRET" not in " ".join(messages)
    assert any("Could not run" in message for message in messages)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_windows_dpapi_and_mutex() -> None:
    """Run the real user-scoped DPAPI and duplicate-instance lock on Windows CI."""
    from app.desktop.platform import acquire_instance, release_instance

    value = Fernet.generate_key()
    encrypted = protect(value)
    assert value not in encrypted and protect(encrypted, decrypt=True) == value
    handle = acquire_instance()
    try:
        with pytest.raises(RuntimeError):
            acquire_instance()
    finally:
        release_instance(handle)


def test_empty_key_is_rejected(tmp_path: Path) -> None:
    """A blank field must not silently disable credential encryption."""
    config = desktop_config(tmp_path)
    config.encryption_key = SecretStr("")
    with pytest.raises(ValueError, match="keys are required"):
        config.settings(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop UI")
def test_setup_window_preserves_keys_and_wires_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise form saves and controls without connecting to a provider."""
    import tkinter as tk

    from app.desktop.window import DesktopWindow

    startup = Mock()
    monkeypatch.setattr("app.desktop.window.set_startup", startup)
    root = tk.Tk()
    try:
        window = DesktopWindow(root, tmp_path, auto_refresh=False)
        synthetic = desktop_config(tmp_path)
        window.database.set(synthetic.database_url.get_secret_value())
        window.encryption.set(synthetic.encryption_key.get_secret_value())
        assert window.save()
        first = storage.load_config(tmp_path)
        assert first is not None
        window.startup.set(True)
        assert window.save()
        startup.assert_called_with(True)
        second = storage.load_config(tmp_path)
        assert second is not None and first.backup_key == second.backup_key
        start = Mock()
        monkeypatch.setattr(window.controller, "start", start)
        window.check()
        assert start.call_args.kwargs == {"check_only": True}
        window.start()
        assert not start.call_args.args[0].live_send_enabled
        root.update()
    finally:
        root.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop UI")
def test_overview_refresh_stale_data_and_connection_generation(tmp_path: Path) -> None:
    """Render real zeros, preserve stale data, and ignore results for a previous workspace."""
    import tkinter as tk
    from datetime import UTC, datetime

    from app.desktop.analytics import SnapshotResult
    from app.desktop.metrics import DailyActivity, DesktopSnapshot
    from app.desktop.window import DesktopWindow

    root = tk.Tk()
    try:
        window = DesktopWindow(root, tmp_path, auto_refresh=False)
        window.show_page("overview")
        assert window.overview.values["sent"].get() == "—"
        snapshot = DesktopSnapshot(
            datetime(2026, 11, 13, tzinfo=UTC),
            "Asia/Karachi",
            "Last 7 days",
            3,
            5,
            2,
            4,
            0,
            1,
            0,
            tuple(DailyActivity(str(i), 0, 0) for i in range(7)),
        )
        window.analytics.results.put(SnapshotResult(0, snapshot))
        window.poll()
        assert window.overview.values["sent"].get() == "5"
        window.analytics.results.put(SnapshotResult(0, None))
        window.poll()
        assert window.overview.values["sent"].get() == "5"
        assert "previous data" in window.overview.updated.get()
        assert window.refresh_due == float("inf")
        window.change_period()
        window.analytics.results.put(SnapshotResult(0, snapshot))
        window.poll()
        assert window.overview.values["sent"].get() == "—"
        assert window.overview.snapshot is None
        root.geometry("980x680")
        root.update()
        assert window.overview.frame.winfo_width() <= root.winfo_width()
    finally:
        root.destroy()
