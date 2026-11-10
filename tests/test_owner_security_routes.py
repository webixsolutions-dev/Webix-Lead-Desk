"""Owner security HTTP forms, rate limiting and disabled-factor device management."""

import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import start_session
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import SESSION_COOKIE, token_hash
from app.db.models import AuditEvent, UserSession
from app.db.security_models import MFAChallenge, OwnerMFA
from app.db.session import Database
from app.security.factors import decrypt
from app.security.login import CHALLENGE_COOKIE
from app.security.totp import code_at
from tests.conftest import Account, csrf_from, sign_in
from tests.test_owner_security import enroll


def test_full_owner_enrollment_and_disable_forms(
    client: TestClient, db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Setup proves possession, shows codes once and never returns passwords or stored secrets."""
    sign_in(client, owner)
    csrf = csrf_from(client.get("/settings/security").text)
    assert (
        client.post(
            "/settings/security/start", data={"csrf_token": csrf, "password": "wrong"}
        ).status_code
        == 400
    )
    response = client.post(
        "/settings/security/start", data={"csrf_token": csrf, "password": owner.password}
    )
    assert response.status_code == 200 and "Verify and enable" in response.text
    assert owner.password not in response.text and response.headers["cache-control"] == "no-store"
    with db.sessions() as session:
        factor = session.get(OwnerMFA, owner.user.id)
        assert factor and factor.pending_secret and not factor.enabled
        secret = decrypt(factor_settings, factor.pending_secret)
    assert secret in response.text and secret not in client.get("/settings/security").text
    response = client.post(
        "/settings/security/confirm",
        data={
            "csrf_token": csrf,
            "password": owner.password,
            "code": code_at(secret, int(utc_now().timestamp()) // 30),
        },
    )
    assert response.status_code == 200 and "Authenticator · On" in response.text
    codes = re.findall(r"<code>([0-9a-f-]{35})</code>", response.text)
    assert len(codes) == 8
    page = client.get("/settings/security")
    assert all(code not in page.text for code in codes) and secret not in page.text
    data = {"csrf_token": csrf, "password": owner.password, "code": codes[0]}
    assert client.post("/settings/security/disable", data=data).status_code == 400
    data["confirmed"] = "true"
    assert "Authenticator · Off" in client.post("/settings/security/disable", data=data).text
    assert client.get("/").status_code == 200
    assert client.post("/settings/security/unknown", data={"csrf_token": csrf}).status_code == 404
    with db.sessions() as session:
        actions = [item.action for item in session.scalars(select(AuditEvent))]
        assert "mfa.enabled" in actions and "security.disable" in actions
        assert secret not in str([item.details for item in session.scalars(select(AuditEvent))])


def test_signout_other_devices_without_enabling_mfa(
    client: TestClient, db: Database, settings: Settings, owner: Account, reviewer: Account
) -> None:
    """Owner can revoke other sessions without enrolling; other users' sessions remain intact."""
    now = utc_now()
    other = start_session(db, settings, owner.user, now)
    other_user = start_session(db, settings, reviewer.user, now)
    sign_in(client, owner)
    current = client.cookies.get(SESSION_COOKIE) or ""
    page = client.get("/settings/security")
    assert "This device" in page.text and "Other device" in page.text
    response = client.post(
        "/settings/security/signout",
        data={"csrf_token": csrf_from(page.text), "password": owner.password, "confirmed": "true"},
    )
    assert response.status_code == 200
    with db.sessions() as session:
        assert session.get(UserSession, token_hash(current))
        assert session.get(UserSession, token_hash(other)) is None
        assert session.get(UserSession, token_hash(other_user))
        assert session.get(OwnerMFA, owner.user.id) is None
    assert client.get("/").status_code == 200


def test_factor_attempt_rate_limit_and_expiry(
    client: TestClient, db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Password verification cannot bypass factor rate limits or challenge expiration."""
    enroll(db, factor_settings, owner)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/login/verify").text)
    for _ in range(factor_settings.login_attempt_limit):
        assert (
            client.post("/login/verify", data={"csrf_token": csrf, "code": "bad"}).status_code
            == 401
        )
    response = client.post("/login/verify", data={"csrf_token": csrf, "code": "bad"})
    assert response.status_code == 429 and "Retry-After" in response.headers
    token = client.cookies.get(CHALLENGE_COOKIE) or ""
    with db.sessions.begin() as session:
        challenge = session.get(MFAChallenge, token_hash(token))
        assert challenge
        challenge.expires_at = utc_now() - timedelta(seconds=1)
    assert client.get("/login/verify").status_code == 303
    assert client.get("/").status_code == 303


def test_security_attempt_rate_limit(
    client: TestClient, owner: Account, settings: Settings
) -> None:
    """Authenticated sessions cannot brute-force password reauthentication."""
    sign_in(client, owner)
    csrf = csrf_from(client.get("/settings/security").text)
    for _ in range(settings.login_attempt_limit):
        assert (
            client.post(
                "/settings/security/start", data={"csrf_token": csrf, "password": "wrong"}
            ).status_code
            == 400
        )
    assert (
        client.post(
            "/settings/security/start", data={"csrf_token": csrf, "password": owner.password}
        ).status_code
        == 429
    )
