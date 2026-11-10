"""Capture optional authenticator forms with disposable synthetic secrets only."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.security_models import OwnerMFA
from app.db.session import Database
from app.security.factors import decrypt
from app.security.totp import code_at


def capture(
    client: TestClient, db: Database, settings: Settings, owner_id: str, password: str, target: Path
) -> None:
    """Exercise real forms on the disposable local fixture database, never real accounts."""
    page = client.get("/settings/security")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    result = client.post(
        "/settings/security/start", data={"csrf_token": csrf[1], "password": password}
    )
    assert result.status_code == 200
    (target / "security-setup.html").write_text(result.text, encoding="utf-8")
    with db.sessions() as session:
        factor = session.get(OwnerMFA, owner_id)
        assert factor and factor.pending_secret
        secret = decrypt(settings, factor.pending_secret)
    result = client.post(
        "/settings/security/confirm",
        data={
            "csrf_token": csrf[1],
            "password": password,
            "code": code_at(secret, int(utc_now().timestamp()) // 30),
        },
    )
    assert result.status_code == 200
    (target / "security-enabled.html").write_text(result.text, encoding="utf-8")
    login = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
    assert csrf
    result = client.post(
        "/login",
        data={"csrf_token": csrf[1], "email": "synthetic@example.test", "password": password},
    )
    assert result.status_code == 303 and result.headers["location"] == "/login/verify"
    result = client.get("/login/verify")
    assert result.status_code == 200
    (target / "verify-login.html").write_text(result.text, encoding="utf-8")
