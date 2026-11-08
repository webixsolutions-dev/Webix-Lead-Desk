"""Write-only credential storage using a dedicated environment-held encryption key."""

from cryptography.fernet import Fernet, InvalidToken

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.delivery_models import IntegrationSecret
from app.db.models import AuditEvent
from app.db.session import Database


def cipher(settings: Settings) -> Fernet:
    """Require a separate key so session-key rotation never decrypts provider credentials."""
    if not settings.encryption_key:
        raise ValueError("Configure ENCRYPTION_KEY privately before saving connections")
    try:
        return Fernet(settings.encryption_key.get_secret_value().encode())
    except ValueError as error:
        raise ValueError("ENCRYPTION_KEY must be a valid Fernet key") from error


def store_secret(db: Database, settings: Settings, key: str, value: str, actor_id: str) -> None:
    """Encrypt a bounded credential; never return or log its plaintext value."""
    if not 1 <= len(value) <= 4096 or not 1 <= len(key) <= 100:
        raise ValueError("Provide a bounded connection credential")
    encrypted = cipher(settings).encrypt(value.encode()).decode()
    with db.sessions.begin() as session:
        item = session.get(IntegrationSecret, key)
        if item is None:
            item = IntegrationSecret(key=key, ciphertext=encrypted, updated_by=actor_id)
            session.add(item)
        else:
            item.ciphertext, item.updated_by, item.updated_at = encrypted, actor_id, utc_now()
        session.add(AuditEvent(actor_id=actor_id, action="connection.saved", entity_id=key))


def read_secret(db: Database, settings: Settings, key: str) -> str:
    """Decrypt inside a provider boundary; callers must not expose or log the result."""
    with db.sessions() as session:
        item = session.get(IntegrationSecret, key)
        if item is None:
            raise ValueError("Connection credential is missing")
        ciphertext = item.ciphertext
    try:
        return cipher(settings).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise ValueError("Connection cannot be decrypted with the configured key") from error


def openai_key(db: Database, settings: Settings) -> str:
    """Resolve an environment key or the owner's encrypted optional API connection."""
    if settings.openai_api_key and settings.openai_api_key.get_secret_value():
        return settings.openai_api_key.get_secret_value()
    return read_secret(db, settings, "openai")
