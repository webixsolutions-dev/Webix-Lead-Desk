"""Isolated migrated databases, synthetic users and an in-process HTTP client."""

import re
import secrets
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.auth import create_user
from app.core.config import Settings
from app.db.migrate import migrate
from app.db.models import User
from app.db.session import Database, create_database
from app.main import create_app


@dataclass(frozen=True)
class Account:
    """A disposable fixture account; no real identity or reusable password."""

    user: User
    password: str


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disallow non-loopback Python socket connections in all automated tests."""
    original = socket.socket.connect

    def guarded(sock: socket.socket, address: Any) -> None:
        """Allow only local database or fake-server traffic."""
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Automated tests must not contact external services")
        original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Use a fresh SQLite database and a random test-only session key."""
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        data_dir=tmp_path,
        session_secret=SecretStr(secrets.token_urlsafe(48)),
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    """Create schema through Alembic only and dispose it after each test."""
    migrate(settings)
    database = create_database(settings)
    yield database
    database.engine.dispose()


@pytest.fixture
def owner(db: Database) -> Account:
    """Create a synthetic owner with a fresh random password."""
    password = secrets.token_urlsafe(24)
    return Account(
        create_user(db, "owner@example.test", "Workspace Owner", password, "owner"), password
    )


@pytest.fixture
def reviewer(db: Database) -> Account:
    """Create a synthetic reviewer with a fresh random password."""
    password = secrets.token_urlsafe(24)
    return Account(
        create_user(db, "reviewer@example.test", "Team Reviewer", password, "reviewer"), password
    )


@pytest.fixture
def client(settings: Settings, db: Database) -> Iterator[TestClient]:
    """Exercise routes in process without starting a real HTTP server."""
    with TestClient(create_app(settings), follow_redirects=False) as http:
        yield http


def csrf_from(html: str) -> str:
    """Extract the server-rendered form token used by a real user submission."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "Expected a CSRF-protected form"
    return match.group(1)


def sign_in(client: TestClient, account: Account) -> None:
    """Complete the public sign-in form with a synthetic fixture account."""
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "email": account.user.email,
            "password": account.password,
            "csrf_token": csrf_from(page.text),
        },
    )
    assert response.status_code == 303
