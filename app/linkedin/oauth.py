"""Session-bound authorization with member confirmation and encrypted token storage."""

import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, select

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.security import token_hash
from app.core.vault import cipher
from app.db.delivery_models import IntegrationSecret
from app.db.linkedin_post_models import PostingConnection, PostingOAuthState
from app.db.session import Database
from app.integrations import linkedin as provider
from app.linkedin.post_access import profile_access
from app.linkedin.quotas import lock_policy


def start(db: Database, settings: Settings, profile_id: str, actor: str, session_token: str) -> str:
    """Issue a short-lived one-use state tied to the current app session and member."""
    if not settings.linkedin_posting_enabled:
        raise ValueError("Configure LinkedIn developer OAuth privately before connecting")
    value = secrets.token_urlsafe(32)
    with db.sessions.begin() as session:
        profile_access(session, profile_id, actor, own=True)
        lock_policy(session, profile_id, utc_now())
        session.execute(delete(PostingOAuthState).where(PostingOAuthState.profile_id == profile_id))
        session.add(
            PostingOAuthState(
                digest=token_hash(value),
                profile_id=profile_id,
                user_id=actor,
                session_digest=token_hash(session_token),
                expires_at=utc_now() + timedelta(minutes=10),
            )
        )
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(
        {
            "response_type": "code",
            "client_id": settings.linkedin_client_id,
            "redirect_uri": settings.public_base_url + "/linkedin/oauth/callback",
            "scope": "openid profile w_member_social",
            "state": value,
        }
    )


def finish(
    db: Database, settings: Settings, state: str, code: str, actor: str, session_token: str
) -> str:
    """Consume state before exchange; authenticate subject via userinfo, never decode raw JWTs."""
    if not settings.linkedin_posting_enabled or not 1 <= len(code) <= 4096 or len(state) > 128:
        raise ValueError("LinkedIn connection needs a new authorization")
    with db.sessions.begin() as session:
        record = session.scalar(
            select(PostingOAuthState).where(PostingOAuthState.digest == token_hash(state))
        )
        if (
            not record
            or record.user_id != actor
            or record.expires_at <= utc_now()
            or (record.session_digest != token_hash(session_token))
        ):
            raise ValueError("Authorization expired or belongs to another session")
        profile_id = record.profile_id
        profile_access(session, profile_id, actor, own=True)
        removed = session.scalar(
            delete(PostingOAuthState)
            .where(PostingOAuthState.digest == record.digest)
            .returning(PostingOAuthState.digest)
        )
        if not removed:
            raise ValueError("Authorization already used")
    assert settings.linkedin_client_secret
    result = provider.exchange(
        code,
        settings.linkedin_client_id,
        settings.linkedin_client_secret.get_secret_value(),
        settings.public_base_url + "/linkedin/oauth/callback",
    )
    token, lifetime = result.get("access_token"), result.get("expires_in")
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 4096
        or (not isinstance(lifetime, int) or not 60 <= lifetime <= 366 * 86400)
    ):
        raise ValueError("LinkedIn did not return a usable expiring token")
    scopes = result.get("scope")
    if scopes is not None and "w_member_social" not in re.split(r"[ ,]+", str(scopes)):
        raise ValueError("Member posting permission was not granted")
    info = provider.userinfo(token)
    subject, name = info.get("sub"), info.get("name")
    if (
        not isinstance(subject, str)
        or not re.fullmatch(r"[\w-]{1,200}", subject)
        or (not isinstance(name, str) or not 1 <= len(name) <= 200)
    ):
        raise ValueError("LinkedIn member identity is unavailable")
    encrypted = cipher(settings).encrypt(token.encode()).decode()
    with db.sessions.begin() as session:
        profile = profile_access(session, profile_id, actor, own=True)
        lock_policy(session, profile_id, utc_now())
        other = session.scalar(
            select(PostingConnection).where(
                PostingConnection.subject == subject, PostingConnection.profile_id != profile_id
            )
        )
        old = session.get(PostingConnection, profile_id)
        if other or (old and old.subject != subject):
            raise ValueError("Use the same real member account assigned to this profile")
        if old is None:
            old = PostingConnection(profile_id=profile_id, subject=subject)
            session.add(old)
        old.member_name, old.profile_url, old.state = name, profile.profile_url, "confirm"
        old.expires_at = utc_now() + timedelta(seconds=lifetime)
        key = "linkedin:" + profile_id
        secret = session.get(IntegrationSecret, key)
        if not secret:
            session.add(IntegrationSecret(key=key, ciphertext=encrypted, updated_by=actor))
        else:
            secret.ciphertext, secret.updated_by, secret.updated_at = encrypted, actor, utc_now()
    return profile_id


def connection_action(db: Database, profile_id: str, actor: str, decision: str) -> None:
    """Member confirms their identity or disconnects; an owner cannot impersonate them."""
    with db.sessions.begin() as session:
        profile = profile_access(session, profile_id, actor, own=True)
        lock_policy(session, profile_id, utc_now())
        item = session.get(PostingConnection, profile_id)
        if not item or decision not in {"confirm", "disconnect"}:
            raise ValueError("Connection unavailable")
        if decision == "confirm":
            if item.expires_at <= utc_now() or item.profile_url != profile.profile_url:
                raise ValueError("Authorize this member again")
            item.state = "active"
        else:
            item.state = "disconnected"
            session.execute(
                delete(IntegrationSecret).where(IntegrationSecret.key == "linkedin:" + profile_id)
            )
