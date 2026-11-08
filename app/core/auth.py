"""Authentication services with durable throttles and revocable sessions."""

from datetime import datetime, timedelta

from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, random_token, token_hash, verify_password
from app.db.models import AuditEvent, LoginThrottle, User, UserSession
from app.db.session import Database

# A random, non-login password supplies comparable work for unknown accounts.
_DUMMY_HASH = hash_password(random_token())


def create_user(db: Database, email: str, name: str, password: str, role: str) -> User:
    """Create a user through the local administrator workflow, never a public route."""
    email = email.strip().casefold()
    if "@" not in email or len(email) > 254 or not 1 <= len(name.strip()) <= 80:
        raise ValueError("Provide a valid email and a name of 1–80 characters")
    if role not in {"owner", "reviewer"}:
        raise ValueError("Choose owner or reviewer")
    user = User(email=email, name=name.strip(), password_hash=hash_password(password), role=role)
    with db.sessions.begin() as session:
        session.add(user)
        session.flush()
        session.add(AuditEvent(actor_id=user.id, action="user.created", entity_id=user.id))
    return user


def _consume_bucket(session: Session, key: str, now: datetime, cutoff: datetime) -> int:
    """Increment one bucket atomically, handling concurrent first insertions."""
    if session.get(LoginThrottle, key) is None:
        try:
            with session.begin_nested():
                session.add(LoginThrottle(key=key, window_start=now, attempts=0))
                session.flush()
        except IntegrityError:
            pass  # Another transaction created the same unique bucket.
    count = session.scalar(
        update(LoginThrottle)
        .where(LoginThrottle.key == key)
        .values(
            attempts=case(
                (LoginThrottle.window_start <= cutoff, 1), else_=LoginThrottle.attempts + 1
            ),
            window_start=case(
                (LoginThrottle.window_start <= cutoff, now), else_=LoginThrottle.window_start
            ),
        )
        .returning(LoginThrottle.attempts)
    )
    if count is None:
        raise RuntimeError("Login rate-limit bucket is unavailable")
    return count


def consume_login_attempt(
    db: Database, settings: Settings, email: str, source: str, now: datetime
) -> bool:
    """Rate-limit account and connection source without storing either in plaintext."""
    cutoff = now - timedelta(seconds=settings.login_window_seconds)
    with db.sessions.begin() as session:
        session.execute(
            delete(LoginThrottle).where(LoginThrottle.window_start < now - timedelta(days=1))
        )
        source_count = _consume_bucket(session, token_hash("ip:" + source), now, cutoff)
        if source_count > settings.ip_login_attempt_limit:
            return False
        account_count = _consume_bucket(session, token_hash("account:" + email), now, cutoff)
        return account_count <= settings.login_attempt_limit


def authenticate(db: Database, email: str, password: str) -> User | None:
    """Return an active matching user without distinguishing unknown accounts."""
    with db.sessions() as session:
        user = session.scalar(select(User).where(User.email == email.strip().casefold()))
        encoded = user.password_hash if user is not None else _DUMMY_HASH
        valid = verify_password(password, encoded)
        if not valid or user is None or not user.active:
            return None
        return user


def start_session(db: Database, settings: Settings, user: User, now: datetime) -> str:
    """Persist only the token hash and return the new bearer token once."""
    token = random_token()
    with db.sessions.begin() as session:
        session.execute(delete(UserSession).where(UserSession.expires_at <= now))
        session.add(
            UserSession(
                token_hash=token_hash(token),
                user_id=user.id,
                csrf_token=random_token(),
                created_at=now,
                expires_at=now + timedelta(hours=settings.session_hours),
            )
        )
        session.add(AuditEvent(actor_id=user.id, action="session.created", entity_id=user.id))
    return token


def revoke_session(db: Database, token: str, actor_id: str) -> None:
    """Revoke a session on the server even if a client retains its old cookie."""
    with db.sessions.begin() as session:
        session.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))
        session.add(AuditEvent(actor_id=actor_id, action="session.revoked"))


def reset_password(db: Database, email: str, password: str) -> None:
    """Replace a local account password and revoke all of its sessions."""
    encoded = hash_password(password)
    with db.sessions.begin() as session:
        user = session.scalar(select(User).where(User.email == email.strip().casefold()))
        if user is None:
            raise ValueError("Account not found")
        user.password_hash = encoded
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        session.add(AuditEvent(actor_id=user.id, action="user.password_reset", entity_id=user.id))
