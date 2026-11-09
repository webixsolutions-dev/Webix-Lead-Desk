"""Validated private configuration with explicit provider and deployment gates."""

from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load environment settings without exposing secrets in representations."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )
    app_mode: Literal["PC", "VPS", "VERCEL"] = "PC"
    database_url: str = Field(default="sqlite:///./var/lead-desk.sqlite", repr=False)
    data_dir: Path = Path("var")
    session_secret: SecretStr
    cookie_secure: bool = False
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    workspace_name: str = Field(default="Webix Lead Desk", min_length=1, max_length=80)
    display_timezone: str = "Asia/Karachi"
    sending_timezone: str = "America/Edmonton"
    preview_enabled: bool = True
    public_base_url: str = Field(default="", max_length=2000)
    live_send_enabled: bool = False
    encryption_key: SecretStr | None = None
    backup_key: SecretStr | None = None
    backup_max_bytes: int = Field(default=100_000_000, ge=100_000, le=1_000_000_000)
    mail_provider: Literal["titan", "hostinger", "gmail"] = "titan"
    mail_smtp_host: str = "smtp.titan.email"
    mail_smtp_port: int = 465
    mail_imap_host: str = "imap.titan.email"
    mail_imap_port: int = 993
    titan_smtp_host: Literal["smtp.titan.email"] = "smtp.titan.email"
    titan_imap_host: Literal["imap.titan.email"] = "imap.titan.email"
    mail_timeout_seconds: int = Field(default=30, ge=5, le=60)
    external_integrations_enabled: bool = False
    openai_enabled: bool = False
    openai_processing: Literal["standard", "batch"] = "standard"
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="", max_length=120)
    openai_audit_model: str = Field(default="gpt-6-luna", max_length=120)
    openai_draft_model: str = Field(default="gpt-6.1-sol", max_length=120)
    openai_audit_input_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_audit_output_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_draft_input_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_draft_output_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_input_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_output_usd_micro_per_million: int = Field(default=0, ge=0)
    openai_max_output_tokens: int = Field(default=1500, ge=500, le=4000)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=30)
    worker_lease_seconds: int = Field(default=30, ge=5, le=300)
    job_lease_seconds: int = Field(default=60, ge=5, le=600)
    job_max_attempts: int = Field(default=3, ge=1, le=5)
    job_retry_seconds: int = Field(default=5, ge=1, le=300)
    session_hours: int = Field(default=8, ge=1, le=24)
    login_window_seconds: int = Field(default=900, ge=60, le=3600)
    login_attempt_limit: int = Field(default=8, ge=1, le=20)
    ip_login_attempt_limit: int = Field(default=30, ge=1, le=100)
    login_csrf_seconds: int = Field(default=1800, ge=60, le=3600)
    max_form_bytes: int = Field(default=16384, ge=1024, le=65536)

    @field_validator("public_base_url")
    @classmethod
    def valid_public_origin(cls, value: str) -> str:
        """Require an explicit HTTPS origin before publishing prospect concept links."""
        from urllib.parse import urlsplit

        from app.sources.validation import website_url

        if not value:
            return ""
        normalized = website_url(value)
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PUBLIC_BASE_URL must be the deployed HTTPS origin without a path")
        return normalized.rstrip("/")

    @field_validator("encryption_key", "backup_key")
    @classmethod
    def valid_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Validate the dedicated credential encryption key without exposing its value."""
        if value and not value.get_secret_value():
            return None
        if value:
            try:
                Fernet(value.get_secret_value().encode())
            except ValueError as error:
                raise ValueError(
                    "ENCRYPTION_KEY and BACKUP_KEY must each be valid Fernet keys"
                ) from error
        return value

    @field_validator("session_secret")
    @classmethod
    def strong_session_secret(cls, value: SecretStr) -> SecretStr:
        """Reject missing or trivially short session-signing keys."""
        if len(value.get_secret_value()) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        return value

    @field_validator("display_timezone", "sending_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Require an installed IANA timezone."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use a valid IANA timezone") from error
        return value

    @model_validator(mode="after")
    def deployment_guards(self) -> Self:
        """Fail closed on unsupported live actions and insecure hosted profiles."""
        if self.external_integrations_enabled:
            raise ValueError(
                "Use provider-specific settings; the legacy integration switch is unavailable"
            )
        if self.live_send_enabled and not self.encryption_key:
            raise ValueError("Live sending requires a dedicated ENCRYPTION_KEY")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must contain explicit hosts")
        if self.app_mode in {"VPS", "VERCEL"} and (
            not self.database_url.startswith("postgresql") or not self.cookie_secure
        ):
            raise ValueError(f"{self.app_mode} profile requires PostgreSQL and secure cookies")
        if self.worker_poll_seconds >= self.worker_lease_seconds:
            raise ValueError("Worker poll interval must be shorter than its lease")
        return self
