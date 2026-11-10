"""Factor replay prevention against the isolated PostgreSQL database used in CI."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import delete

from app.core.config import Settings
from app.core.security import hash_password, random_token, token_hash
from app.db.models import AuditEvent, User
from app.db.security_models import MFAChallenge
from app.db.session import Database
from app.security.login import begin_login, finish_login
from app.security.totp import code_at
from tests.conftest import Account
from tests.test_hosted_postgres import postgres_db as postgres_db
from tests.test_owner_security import enroll


@pytest.mark.postgres
def test_one_factor_cannot_complete_two_concurrent_challenges(
    postgres_db: Database, factor_settings: Settings
) -> None:
    """Distinct password-verified challenges sharing one OTP cannot both issue a session."""
    db = postgres_db
    password = random_token()
    with db.sessions.begin() as session:
        user = User(
            email="mfa-concurrency@example.test",
            name="Synthetic owner",
            password_hash=hash_password(password),
            role="owner",
        )
        session.add(user)
        session.flush()
    try:
        _, secret, _, now = enroll(db, factor_settings, Account(user, password))
        now += timedelta(seconds=31)
        challenges = []
        for _ in range(2):
            with db.sessions.begin() as session:
                token, _ = begin_login(session, factor_settings, user, now)
                session.flush()
                record = session.get(MFAChallenge, token_hash(token))
                assert record
                challenges.append((token, record.csrf_token))
        code = code_at(secret, int(now.timestamp()) // 30)

        def attempt(item: tuple[str, str]) -> bool:
            try:
                with db.sessions.begin() as session:
                    finish_login(session, factor_settings, item[0], item[1], code, now)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(attempt, challenges)) == [False, True]
    finally:
        with db.sessions.begin() as session:
            session.execute(delete(AuditEvent).where(AuditEvent.actor_id == user.id))
            session.execute(delete(User).where(User.id == user.id))
