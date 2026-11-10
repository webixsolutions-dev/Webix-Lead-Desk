"""Optional MFA protects real login boundaries, replay and device isolation."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.auth import reset_password, start_session
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import SESSION_COOKIE, random_token, token_hash
from app.db.models import UserSession
from app.db.security_models import MFAChallenge, MFARecovery, OwnerMFA
from app.db.session import Database
from app.security import management
from app.security.factors import decrypt
from app.security.login import CHALLENGE_COOKIE, begin_login, finish_login
from app.security.totp import code_at, match_counter
from tests.conftest import Account, csrf_from, sign_in


def enroll(
    db: Database, settings: Settings, owner: Account
) -> tuple[str, str, list[str], datetime]:
    """Enroll an owner using a current authenticated device at a stable test time."""
    now = utc_now().replace(microsecond=0)
    token = start_session(db, settings, owner.user, now)
    with db.sessions.begin() as session:
        secret = management.start_enrollment(
            session, settings, owner.user.id, token, owner.password, now
        )
    with db.sessions.begin() as session:
        codes = management.confirm_enrollment(
            session,
            settings,
            owner.user.id,
            token,
            owner.password,
            code_at(secret, int(now.timestamp()) // 30),
            now,
        )
    return token, secret, codes, now


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors(timestamp: int, expected: str) -> None:
    """Match every published RFC 6238 SHA-1 test vector."""
    key = base64.b32encode(b"12345678901234567890").decode()
    assert code_at(key, timestamp // 30, digits=8) == expected
    code = code_at(key, timestamp // 30)
    assert match_counter(key, code, timestamp, -1) == timestamp // 30
    assert match_counter(key, code, timestamp, timestamp // 30) is None
    assert match_counter(key, "１２３４５６", timestamp, -1) is None
    assert match_counter(key, code, timestamp + 120, -1) is None


def test_optional_default_and_owner_access(
    client: TestClient, owner: Account, reviewer: Account, db: Database
) -> None:
    """Existing password sign-in works; other roles and missing CSRF cannot change security."""
    assert client.get("/settings/security").status_code == 303
    sign_in(client, reviewer)
    assert client.get("/settings/security").status_code == 403
    assert client.post("/settings/security/start", data={}).status_code == 403
    sign_in(client, owner)
    page = client.get("/settings/security")
    assert page.status_code == 200 and "Authenticator · Off" in page.text
    assert (
        client.post("/settings/security/start", data={"password": owner.password}).status_code
        == 403
    )
    with db.sessions() as session:
        assert session.get(OwnerMFA, owner.user.id) is None


def test_enrollment_bound_to_device_and_expiry(
    db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Pending setup is encrypted, disabled and bound to the initiating current device."""
    now = utc_now()
    one = start_session(db, factor_settings, owner.user, now)
    two = start_session(db, factor_settings, owner.user, now)
    with db.sessions.begin() as session:
        secret = management.start_enrollment(
            session, factor_settings, owner.user.id, one, owner.password, now
        )
    with db.sessions() as session:
        factor = session.get(OwnerMFA, owner.user.id)
        assert factor and not factor.enabled and factor.pending_secret != secret
        assert decrypt(factor_settings, factor.pending_secret or "") == secret
    for token, at in [(two, now), (one, now + timedelta(minutes=11))]:
        with pytest.raises(ValueError), db.sessions.begin() as session:
            management.confirm_enrollment(
                session,
                factor_settings,
                owner.user.id,
                token,
                owner.password,
                code_at(secret, int(at.timestamp()) // 30),
                at,
            )
    with pytest.raises(ValueError), db.sessions.begin() as session:
        management.confirm_enrollment(
            session, factor_settings, owner.user.id, one, "wrong", "000000", now
        )


def test_http_password_requires_factor_and_recovery_is_single_use(
    client: TestClient, db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Password creates only a pending challenge; a recovery code grants exactly one session."""
    token, secret, codes, now = enroll(db, factor_settings, owner)
    sign_in(client, owner)
    assert not client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get(CHALLENGE_COOKIE)
    assert client.get("/").status_code == 303
    page = client.get("/login/verify")
    assert page.status_code == 200 and secret not in page.text
    assert client.post("/login/verify", data={"code": codes[0]}).status_code == 403
    csrf = csrf_from(page.text)
    assert (
        client.post("/login/verify", data={"csrf_token": csrf, "code": "wrong"}).status_code == 401
    )
    response = client.post("/login/verify", data={"csrf_token": csrf, "code": codes[0]})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client.get("/").status_code == 200
    assert not client.cookies.get(CHALLENGE_COOKIE)
    assert (
        client.post("/login/verify", data={"csrf_token": csrf, "code": codes[0]}).status_code == 303
    )
    with pytest.raises(ValueError):
        start_session(db, factor_settings, owner.user, now)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/login/verify").text)
    assert (
        client.post("/login/verify", data={"csrf_token": csrf, "code": codes[0]}).status_code == 401
    )
    with db.sessions() as session:
        assert all(item.code_hash not in codes for item in session.scalars(select(MFARecovery)))
        assert session.get(UserSession, token_hash(token)) is not None


def test_totp_challenge_atomic_replay_and_password_reset(
    db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Two simultaneous uses of one challenge produce one session; reset keeps MFA enabled."""
    _, secret, _, now = enroll(db, factor_settings, owner)
    later = now + timedelta(seconds=31)
    with db.sessions.begin() as session:
        challenge_token, required = begin_login(session, factor_settings, owner.user, later)
        assert required
    with db.sessions() as session:
        item = session.get(MFAChallenge, token_hash(challenge_token))
        assert item
        csrf = item.csrf_token
    code = code_at(secret, int(later.timestamp()) // 30)

    def attempt() -> bool:
        try:
            with db.sessions.begin() as session:
                finish_login(session, factor_settings, challenge_token, csrf, code, later)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: attempt(), range(2))) == [False, True]
    with db.sessions.begin() as session:
        pending, _ = begin_login(session, factor_settings, owner.user, later)
    reset_password(db, owner.user.email, random_token())
    with pytest.raises(ValueError), db.sessions.begin() as session:
        finish_login(session, factor_settings, pending, csrf, code, later)
    with db.sessions() as session:
        factor = session.get(OwnerMFA, owner.user.id)
        assert factor and factor.enabled
        assert session.scalar(select(UserSession)) is None


def test_manage_factors_and_other_devices(
    db: Database, factor_settings: Settings, owner: Account, reviewer: Account
) -> None:
    """Owner actions preserve this device and other accounts, revoking other owner devices."""
    other_user = start_session(db, factor_settings, reviewer.user, utc_now())
    current, _, codes, now = enroll(db, factor_settings, owner)
    with db.sessions.begin() as session:
        pending, _ = begin_login(session, factor_settings, owner.user, now)
        session.flush()
        item = session.get(MFAChallenge, token_hash(pending))
        assert item
        second = finish_login(session, factor_settings, pending, item.csrf_token, codes[0], now)
    with pytest.raises(ValueError), db.sessions.begin() as session:
        management.manage(
            session,
            factor_settings,
            owner.user.id,
            current,
            owner.password,
            "wrong",
            "disable",
            now,
        )
    with db.sessions.begin() as session:
        replacement = management.manage(
            session,
            factor_settings,
            owner.user.id,
            current,
            owner.password,
            codes[1],
            "recovery",
            now,
        )
    assert len(replacement) == 8 and not set(replacement).intersection(codes)
    with db.sessions() as session:
        assert session.get(UserSession, token_hash(current))
        assert session.get(UserSession, token_hash(other_user))
        assert session.get(UserSession, token_hash(second)) is None
    with pytest.raises(ValueError), db.sessions.begin() as session:
        management.manage(
            session,
            factor_settings,
            owner.user.id,
            current,
            owner.password,
            codes[2],
            "disable",
            now,
        )
    with db.sessions.begin() as session:
        management.manage(
            session,
            factor_settings,
            owner.user.id,
            current,
            owner.password,
            replacement[0],
            "disable",
            now,
        )
    with db.sessions() as session:
        factor = session.get(OwnerMFA, owner.user.id)
        assert factor and not factor.enabled and not factor.encrypted_secret
        assert session.scalar(select(MFARecovery)) is None
    assert start_session(db, factor_settings, owner.user, now)


def test_expired_challenge_and_rotated_encryption_key(
    db: Database, factor_settings: Settings, owner: Account
) -> None:
    """Expired challenges and undecryptable authenticator secrets fail closed."""
    _, secret, _, now = enroll(db, factor_settings, owner)
    with db.sessions.begin() as session:
        token, _ = begin_login(session, factor_settings, owner.user, now)
    with db.sessions() as session:
        item = session.get(MFAChallenge, token_hash(token))
        assert item
        csrf = item.csrf_token
    with pytest.raises(ValueError), db.sessions.begin() as session:
        finish_login(session, factor_settings, token, csrf, "000000", now + timedelta(minutes=6))
    factor_settings.encryption_key = SecretStr(Fernet.generate_key().decode())
    later = now + timedelta(seconds=31)
    with pytest.raises(ValueError), db.sessions.begin() as session:
        finish_login(
            session,
            factor_settings,
            token,
            csrf,
            code_at(secret, int(later.timestamp()) // 30),
            later,
        )


def test_backup_retains_factor_but_omits_signin_challenges(
    db: Database, factor_settings: Settings, owner: Account, tmp_path: Path
) -> None:
    """Recovery preserves active MFA while blocking transfer of partial sign-ins and wrong keys."""
    from app.operations.backups import export_backup, read_backup
    from app.operations.control import set_maintenance
    from tests.test_operations import backup_config

    _, secret, _, now = enroll(db, factor_settings, owner)
    with db.sessions.begin() as session:
        begin_login(session, factor_settings, owner.user, now)
    config = backup_config(factor_settings)
    set_maintenance(db, True, "Synthetic security restore check")
    path = tmp_path / "security.wld"
    export_backup(db, config, path, offline_confirmed=True)
    tables = read_backup(config, path)
    assert not tables["sessions"] and not tables["mfa_challenges"]
    assert tables["owner_mfa"][0]["enabled"] and tables["owner_mfa"][0]["pending_secret"] is None
    assert decrypt(config, tables["owner_mfa"][0]["encrypted_secret"]) == secret
    wrong = config.model_copy(update={"encryption_key": SecretStr(Fernet.generate_key().decode())})
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        read_backup(wrong, path)
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        export_backup(db, wrong, tmp_path / "wrong.wld", offline_confirmed=True)
