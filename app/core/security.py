"""Password hashing, opaque sessions and signed login CSRF tokens."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import Settings

PASSWORD_HASHER = PasswordHasher()
SESSION_COOKIE = "webix_session"
LOGIN_CSRF_COOKIE = "webix_login_csrf"


def token_hash(value: str) -> str:
    """Create a fixed-length identifier for a high-entropy session token."""
    return hashlib.sha256(value.encode()).hexdigest()


def random_token() -> str:
    """Generate a cryptographically random bearer or CSRF token."""
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    """Hash a password after enforcing length bounds."""
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a password between 12 and 256 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Verify a bounded password without leaking library error details."""
    if len(password) > 256:
        return False
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def make_login_csrf(settings: Settings) -> str:
    """Sign a one-page login token to prevent login CSRF."""
    signer = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="login-csrf")
    return signer.dumps(random_token())


def valid_login_csrf(settings: Settings, cookie: str, submitted: str) -> bool:
    """Validate both cookie binding and signed token age."""
    if not cookie or not hmac.compare_digest(cookie, submitted):
        return False
    signer = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="login-csrf")
    try:
        signer.loads(cookie, max_age=settings.login_csrf_seconds)
    except BadData:
        return False
    return True
