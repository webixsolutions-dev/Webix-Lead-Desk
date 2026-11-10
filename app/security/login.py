"""Password-first login challenges that cannot grant access before the second factor."""

import hmac
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import random_token, token_hash
from app.db.models import User
from app.db.security_models import MFAChallenge, OwnerMFA
from app.security.factors import consume_factor
from app.security.sessions import issue_session, lock_user

CHALLENGE_COOKIE = "webix_mfa_challenge"


def begin_login(
    session: Session,
    settings: Settings,
    authenticated: User,
    now: datetime,
    user_agent: str = "",
) -> tuple[str, bool]:
    """Recheck the password version under lock and return either a session or challenge."""
    user = lock_user(session, authenticated.id)
    if user.password_hash != authenticated.password_hash:
        raise ValueError("Sign-in is no longer valid. Start again.")
    factor = session.get(OwnerMFA, user.id, populate_existing=True)
    if factor is None or not factor.enabled:
        return issue_session(session, settings, user, now, user_agent), False
    session.execute(delete(MFAChallenge).where(MFAChallenge.expires_at <= now))
    token = random_token()
    session.add(
        MFAChallenge(
            token_hash=token_hash(token),
            user_id=user.id,
            csrf_token=random_token(),
            expires_at=now + timedelta(minutes=5),
            password_fingerprint=token_hash(user.password_hash),
            mfa_version=factor.version,
        )
    )
    return token, True


def challenge(session: Session, token: str, now: datetime) -> MFAChallenge:
    """Read a bounded active challenge; never accept it as a full authentication token."""
    if not token or len(token) > 128:
        raise ValueError("Sign-in check expired. Start again.")
    item = session.get(MFAChallenge, token_hash(token), populate_existing=True)
    if item is None or item.expires_at <= now:
        raise ValueError("Sign-in check expired. Start again.")
    return item


def finish_login(
    session: Session,
    settings: Settings,
    token: str,
    csrf: str,
    supplied: str,
    now: datetime,
    user_agent: str = "",
) -> str:
    """Consume one challenge and factor, and issue the session in the same transaction."""
    item = challenge(session, token, now)
    user = lock_user(session, item.user_id)
    item = challenge(session, token, now)  # Recheck after waiting for another device's mutation.
    factor = session.get(OwnerMFA, user.id, populate_existing=True)
    if not csrf or not hmac.compare_digest(csrf, item.csrf_token):
        raise ValueError("Sign-in check expired. Start again.")
    if (
        factor is None
        or not factor.enabled
        or factor.version != item.mfa_version
        or item.password_fingerprint != token_hash(user.password_hash)
    ):
        raise ValueError("Sign-in changed. Start again.")
    consume_factor(session, settings, factor, supplied, now)
    session.delete(item)
    return issue_session(session, settings, user, now, user_agent)
