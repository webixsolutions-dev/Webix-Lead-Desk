"""Exercise the configured cloud entry point without connecting to live services."""

import importlib
import secrets
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine


@pytest.fixture
def vercel_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Provide synthetic hosted settings, isolated from the developer's .env file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/lead_desk_test")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("ALLOWED_HOSTS", '["desk.example.test"]')
    # Importing the cloud entry point must not permit a copied local profile.
    monkeypatch.setenv("APP_MODE", "PC")
    sys.modules.pop("app.vercel", None)


def test_configured_vercel_entrypoint_exports_app_without_connecting(
    vercel_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Vercel can load the real app; initialization neither connects nor migrates."""

    def forbidden_connection(self: Engine) -> None:
        """Fail even for loopback database access while importing the module."""
        raise AssertionError("Entrypoint import must not open a database connection")

    monkeypatch.setattr(Engine, "connect", forbidden_connection)
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        entrypoint = tomllib.load(stream)["tool"]["vercel"]["entrypoint"]
    module_name, attribute = entrypoint.split(":")
    module = importlib.import_module(module_name)
    app = getattr(module, attribute)
    try:
        assert isinstance(app, FastAPI)
        assert app.state.services.settings.app_mode == "VERCEL"
        assert app.state.services.settings.cookie_secure
        assert app.state.services.settings.allowed_hosts == ["desk.example.test"]
        assert app.state.services.db.engine.url.get_backend_name() == "postgresql"
        # These public routes need no database. Lifespan/database startup is
        # exercised separately by the migrated database tests, not this import test.
        client = TestClient(app, base_url="https://desk.example.test")
        try:
            login = client.get("/login")
            assert login.status_code == 200
            assert "Secure" in login.headers["set-cookie"]
            assert client.get("/static/webix-logo.png").status_code == 200
            assert client.get("/health/live").json()["status"] == "ok"
            assert (
                client.get("/login", headers={"host": "untrusted.example.test"}).status_code == 400
            )
        finally:
            client.close()
        assert not list(tmp_path.iterdir())
    finally:
        app.state.services.db.engine.dispose()
        sys.modules.pop(module_name, None)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("DATABASE_URL", "sqlite:////tmp/lead-desk.sqlite", "VERCEL profile"),
        ("COOKIE_SECURE", "false", "VERCEL profile"),
        ("SESSION_SECRET", "", "SESSION_SECRET"),
    ],
)
def test_vercel_rejects_unsafe_configuration(
    vercel_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
    message: str,
) -> None:
    """A missing secret, ephemeral database or insecure cookie fails before serving."""
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError, match=message):
        importlib.import_module("app.vercel")
