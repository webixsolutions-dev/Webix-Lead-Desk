"""Owner-only enrollment, recovery rotation and device revocation."""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import token_hash, verify_password
from app.core.vault import cipher
from app.db.models import AuditEvent, User, UserSession
from app.db.security_models import MFAChallenge, MFARecovery, OwnerMFA
from app.security.factors import consume_factor, decrypt, replace_recovery
from app.security.sessions import lock_user
from app.security.totp import match_counter, new_secret


def reauthenticate(
    session: Session, user_id: str, current_token: str, password: str, now: datetime
) -> User:
    """Require an active owner, current device and password for security mutations."""
    user = lock_user(session, user_id)
    device = session.get(UserSession, token_hash(current_token), populate_existing=True)
    if (
        user.role != "owner"
        or device is None
        or device.user_id != user.id
        or device.expires_at <= now
        or not verify_password(password, user.password_hash)
    ):
        raise ValueError("Verify your current owner password and sign-in session.")
    return user


def start_enrollment(
    session: Session,
    settings: Settings,
    user_id: str,
    current_token: str,
    password: str,
    now: datetime,
) -> str:
    """Prepare a ten-minute encrypted secret for this device, still disabled."""
    reauthenticate(session, user_id, current_token, password, now)
    factor = session.get(OwnerMFA, user_id, populate_existing=True)
    if factor is not None and factor.enabled:
        raise ValueError("Authenticator is already enabled. Disable it before replacing it.")
    if factor is None:
        factor = OwnerMFA(user_id=user_id)
        session.add(factor)
    secret = new_secret()
    factor.pending_secret = cipher(settings).encrypt(secret.encode()).decode()
    factor.pending_expires = now + timedelta(minutes=10)
    factor.pending_session_hash = token_hash(current_token)
    session.add(AuditEvent(actor_id=user_id, action="mfa.enrollment_started", entity_id=user_id))
    return secret


def revoke_others(session: Session, user_id: str, current_token: str) -> None:
    """Revoke this owner's other devices and all unfinished sign-ins under its user lock."""
    session.execute(
        delete(UserSession).where(
            UserSession.user_id == user_id, UserSession.token_hash != token_hash(current_token)
        )
    )
    session.execute(delete(MFAChallenge).where(MFAChallenge.user_id == user_id))


def confirm_enrollment(
    session: Session,
    settings: Settings,
    user_id: str,
    current_token: str,
    password: str,
    supplied: str,
    now: datetime,
) -> list[str]:
    """Enable only after the owner proves the new factor; return private recovery codes once."""
    reauthenticate(session, user_id, current_token, password, now)
    factor = session.get(OwnerMFA, user_id, populate_existing=True)
    if (
        factor is None
        or factor.enabled
        or not factor.pending_secret
        or not factor.pending_expires
        or factor.pending_expires <= now
        or factor.pending_session_hash != token_hash(current_token)
    ):
        raise ValueError("Enrollment expired or belongs to another device. Start again.")
    counter = match_counter(decrypt(settings, factor.pending_secret), supplied, now.timestamp(), -1)
    if counter is None:
        raise ValueError("Enter the six-digit code from the authenticator you just added.")
    factor.encrypted_secret = factor.pending_secret
    factor.pending_secret = factor.pending_expires = factor.pending_session_hash = None
    factor.enabled = True
    factor.last_counter = counter
    factor.version += 1
    codes = replace_recovery(session, user_id)
    revoke_others(session, user_id, current_token)
    session.add(AuditEvent(actor_id=user_id, action="mfa.enabled", entity_id=user_id))
    return codes


def manage(
    session: Session,
    settings: Settings,
    user_id: str,
    current_token: str,
    password: str,
    supplied: str,
    action: str,
    now: datetime,
) -> list[str]:
    """Reauthenticate before factor removal, code replacement or other-device sign-out."""
    if action not in {"disable", "recovery", "signout"}:
        raise ValueError("Choose an available security action.")
    reauthenticate(session, user_id, current_token, password, now)
    factor = session.get(OwnerMFA, user_id, populate_existing=True)
    if factor is not None and factor.enabled:
        consume_factor(session, settings, factor, supplied, now)
        factor.version += 1
    elif action != "signout":
        raise ValueError("Authenticator is not enabled.")
    codes: list[str] = []
    if action == "disable" and factor is not None:
        factor.enabled = False
        factor.encrypted_secret = None
        factor.last_counter = -1
        session.execute(delete(MFARecovery).where(MFARecovery.user_id == user_id))
    if action == "recovery":
        codes = replace_recovery(session, user_id)
    if factor is not None:
        factor.pending_secret = factor.pending_expires = factor.pending_session_hash = None
    revoke_others(session, user_id, current_token)
    session.add(AuditEvent(actor_id=user_id, action="security." + action, entity_id=user_id))
    return codes
