"""Local administrator workflows with synthetic secrets and isolated databases."""

import getpass
import secrets
import sys

import pytest
from sqlalchemy import select

from app import cli
from app.core.config import Settings
from app.db.models import Job, User
from app.db.session import Database


def invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> int:
    """Run the real CLI dispatcher without spawning another process."""
    monkeypatch.setattr(sys, "argv", ["lead-desk", *args])
    return cli.main()


def test_local_admin_and_worker_workflow(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Migrate, create an owner, reset a password and process a durable health job."""
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: password)
    assert invoke(monkeypatch, ["migrate"]) == 0
    args = ["create-user", "--email", "admin@example.test", "--name", "Local Admin"]
    assert invoke(monkeypatch, args) == 0
    assert invoke(monkeypatch, args) == 2
    assert invoke(monkeypatch, ["reset-password", "--email", "admin@example.test"]) == 0
    assert invoke(monkeypatch, ["check"]) == 0
    assert invoke(monkeypatch, ["enqueue-health"]) == 0
    assert invoke(monkeypatch, ["worker", "--once"]) == 0
    with db.sessions() as session:
        assert session.scalar(select(User)) is not None
        job = session.scalar(select(Job))
        assert job is not None and job.state == "succeeded"
    assert password not in caplog.text


def test_confirmation_and_invalid_config(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Mismatched passwords and invalid configuration fail without revealing values."""
    answers = iter(["first-password", "second-password"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(ValueError, match="do not match"):
        cli.confirmed_password()

    def fail_config() -> Settings:
        """Simulate a configuration error containing a non-loggable value."""
        raise ValueError("sensitive configuration value")

    monkeypatch.setattr(cli, "Settings", fail_config)
    assert invoke(monkeypatch, ["check"]) == 2
