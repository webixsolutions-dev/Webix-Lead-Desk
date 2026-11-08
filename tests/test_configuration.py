"""Fail-closed configuration and secret handling."""

import secrets
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.security import hash_password, make_login_csrf, valid_login_csrf, verify_password


@pytest.mark.parametrize("option", ["live_send_enabled", "external_integrations_enabled"])
def test_integrations_require_explicit_configuration(option: str) -> None:
    """A config edit cannot bypass the absence of approved integration code."""
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY|legacy integration"):
        Settings(
            _env_file=None,
            session_secret=SecretStr(secrets.token_urlsafe(48)),
            live_send_enabled=option == "live_send_enabled",
            external_integrations_enabled=option == "external_integrations_enabled",
        )


def test_vps_profile_requires_postgres_and_https() -> None:
    """Mode selection validates prerequisites instead of pretending to migrate data."""
    with pytest.raises(ValidationError, match="VPS profile"):
        Settings(
            _env_file=None, session_secret=SecretStr(secrets.token_urlsafe(48)), app_mode="VPS"
        )


def test_unsafe_configuration_is_rejected() -> None:
    """Reject weak secrets, arbitrary hosts, invalid zones and broken lease settings."""
    secret = SecretStr(secrets.token_urlsafe(48))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, session_secret=SecretStr("short"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, session_secret=secret, allowed_hosts=["*"])
    with pytest.raises(ValidationError):
        Settings(_env_file=None, session_secret=secret, display_timezone="Imaginary/City")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, session_secret=secret, worker_poll_seconds=30, worker_lease_seconds=5
        )


def test_sensitive_config_is_not_in_repr(tmp_path: Path) -> None:
    """Normal configuration representations redact secrets and connection strings."""
    secret = secrets.token_urlsafe(48)
    settings = Settings(
        _env_file=None,
        session_secret=SecretStr(secret),
        database_url=f"sqlite:///{tmp_path / 'sensitive.sqlite'}",
    )
    assert secret not in repr(settings)
    assert "sensitive.sqlite" not in repr(settings)


def test_password_hash_and_login_csrf(settings: Settings) -> None:
    """Password checks and token signatures reject incorrect or forged input."""
    password = secrets.token_urlsafe(24)
    encoded = hash_password(password)
    assert encoded != password and verify_password(password, encoded)
    assert not verify_password("incorrect", encoded)
    assert not verify_password("x" * 257, encoded)
    assert not verify_password(password, "not-a-hash")
    with pytest.raises(ValueError):
        hash_password("short")
    signed = make_login_csrf(settings)
    assert valid_login_csrf(settings, signed, signed)
    assert not valid_login_csrf(settings, signed, "forged")
    assert not valid_login_csrf(settings, "forged", "forged")
