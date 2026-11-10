"""Serialized account changes and shared authenticated session creation."""

from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import random_token, token_hash
from app.db.models import AuditEvent, User, UserSession


def lock_user(session: Session, user_id: str) -> User:
    """Serialize authentication and security changes on both SQLite and PostgreSQL."""
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    user = session.get(User, user_id, populate_existing=True)
    if user is None or not user.active:
        raise ValueError("Sign-in is no longer valid. Start again.")
    return user


def issue_session(
    session: Session, settings: Settings, user: User, now: datetime, user_agent: str = ""
) -> str:
    """Issue a session only inside a successfully verified, locked account transaction."""
    token = random_token()
    session.execute(delete(UserSession).where(UserSession.expires_at <= now))
    session.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            csrf_token=random_token(),
            created_at=now,
            last_seen_at=now,
            user_agent="".join(c for c in user_agent if c.isprintable())[:200],
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    session.add(AuditEvent(actor_id=user.id, action="session.created", entity_id=user.id))
    return token
