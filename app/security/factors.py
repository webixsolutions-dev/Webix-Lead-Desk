"""Factor verification and recovery codes; callers hold the owning user's lock."""

import secrets
from datetime import datetime

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import token_hash
from app.core.vault import cipher
from app.db.security_models import MFARecovery, OwnerMFA
from app.security.totp import match_counter


def decrypt(settings: Settings, encrypted: str) -> str:
    """Decrypt a secret without exposing key material in errors."""
    try:
        return cipher(settings).decrypt(encrypted.encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise ValueError(
            "Authenticator key unavailable. Restore the configured encryption key."
        ) from error


def recovery_hash(code: str) -> str:
    """Normalize a random recovery code without weakening its 128 bits of entropy."""
    return token_hash(code.replace("-", "").replace(" ", "").lower())


def replace_recovery(session: Session, user_id: str) -> list[str]:
    """Replace all old codes and return the new plaintext codes once."""
    session.execute(delete(MFARecovery).where(MFARecovery.user_id == user_id))
    codes = []
    for _ in range(8):
        raw = secrets.token_hex(16)
        code = "-".join(raw[i : i + 8] for i in range(0, 32, 8))
        session.add(MFARecovery(user_id=user_id, code_hash=recovery_hash(code)))
        codes.append(code)
    return codes


def consume_factor(
    session: Session, settings: Settings, factor: OwnerMFA, supplied: str, now: datetime
) -> None:
    """Consume one authenticator counter or recovery code atomically under the user lock."""
    if not factor.enabled or not factor.encrypted_secret or not 1 <= len(supplied) <= 80:
        raise ValueError("Enter a valid unused authenticator or recovery code.")
    if len(supplied) == 6 and supplied.isdigit():
        counter = match_counter(
            decrypt(settings, factor.encrypted_secret),
            supplied,
            now.timestamp(),
            factor.last_counter,
        )
        if counter is None:
            raise ValueError("Enter a valid unused code; wait for the next authenticator code.")
        factor.last_counter = counter
    else:
        recovery = session.scalar(
            select(MFARecovery).where(
                MFARecovery.user_id == factor.user_id,
                MFARecovery.code_hash == recovery_hash(supplied),
                MFARecovery.used_at.is_(None),
            )
        )
        if recovery is None:
            raise ValueError("Enter a valid unused authenticator or recovery code.")
        recovery.used_at = now
