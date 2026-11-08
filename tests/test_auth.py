"""Authentication behavior, rather than implementation-shaped mocks."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import reset_password
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import SESSION_COOKIE, random_token, token_hash
from app.db.models import User, UserSession
from app.db.session import Database
from tests.conftest import Account, csrf_from, sign_in


def test_private_pages_require_session(client: TestClient) -> None:
    """All six tabs reject anonymous access without revealing preview or account data."""
    for path in ["/", "/leads", "/outbox", "/pipeline", "/demos", "/settings"]:
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert "Prairie Peak" not in response.text


def test_session_stores_hash_and_logout_revokes(
    client: TestClient, owner: Account, db: Database
) -> None:
    """A stolen old cookie becomes unusable after server-side logout."""
    sign_in(client, owner)
    token = client.cookies.get(SESSION_COOKIE)
    assert token is not None
    with db.sessions() as session:
        record = session.scalar(select(UserSession))
        assert record is not None and record.token_hash == token_hash(token)
        assert record.token_hash != token
    page = client.get("/")
    response = client.post("/logout", data={"csrf_token": csrf_from(page.text)})
    assert response.status_code == 303
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/").status_code == 303


def test_login_and_logout_reject_missing_csrf(client: TestClient, owner: Account) -> None:
    """Both login and authenticated state-changing actions require valid CSRF."""
    assert (
        client.post(
            "/login", data={"email": owner.user.email, "password": owner.password}
        ).status_code
        == 403
    )
    sign_in(client, owner)
    assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert client.get("/").status_code == 200


def test_expired_session_is_rejected(client: TestClient, owner: Account, db: Database) -> None:
    """Expired sessions cannot access pages even when their cookie remains."""
    sign_in(client, owner)
    with db.sessions.begin() as session:
        record = session.scalar(select(UserSession))
        assert record is not None
        record.expires_at = utc_now() - timedelta(seconds=1)
    assert client.get("/").status_code == 303


def test_inactive_user_and_password_reset_revoke_access(
    client: TestClient, owner: Account, db: Database
) -> None:
    """Account disablement is immediate and reset invalidates all existing sessions."""
    sign_in(client, owner)
    with db.sessions.begin() as session:
        user = session.get(User, owner.user.id)
        assert user is not None
        user.active = False
    assert client.get("/").status_code == 303
    with db.sessions.begin() as session:
        user = session.get(User, owner.user.id)
        assert user is not None
        user.active = True
    reset_password(db, owner.user.email, random_token())
    assert client.get("/").status_code == 303


def test_login_throttles_without_logging_password(
    client: TestClient, owner: Account, settings: Settings
) -> None:
    """Repeated wrong credentials hit an account limit with a generic response."""
    for _ in range(settings.login_attempt_limit):
        token = csrf_from(client.get("/login").text)
        response = client.post(
            "/login", data={"email": owner.user.email, "password": "wrong", "csrf_token": token}
        )
        assert response.status_code == 401
    token = csrf_from(client.get("/login").text)
    blocked = client.post(
        "/login", data={"email": owner.user.email, "password": owner.password, "csrf_token": token}
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert owner.password not in blocked.text


def test_cookie_and_security_headers(client: TestClient, owner: Account) -> None:
    """Sessions use browser protections and private pages are not cacheable."""
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login", data={"email": owner.user.email, "password": owner.password, "csrf_token": token}
    )
    cookie = response.headers.get_list("set-cookie")[0]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    page = client.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400


def test_oversized_and_ambiguous_forms_rejected(client: TestClient) -> None:
    """Bound request parsing and reject duplicate fields before authentication."""
    headers = {"content-type": "application/x-www-form-urlencoded"}
    assert client.post("/login", content="a=" + "x" * 20000, headers=headers).status_code == 413
    assert client.post("/login", content="email=a&email=b", headers=headers).status_code == 400
    assert client.post("/login", json={"email": "x"}).status_code == 415
