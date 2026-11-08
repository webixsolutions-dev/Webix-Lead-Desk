"""Validated deployment configuration; Phase 1 has no live integrations."""

from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    live_send_enabled: bool = False
    external_integrations_enabled: bool = False
    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="", max_length=120)
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
    def phase_one_guards(self) -> Self:
        """Fail closed on unsupported live actions and insecure hosted profiles."""
        if self.live_send_enabled or self.external_integrations_enabled:
            raise ValueError("Live integrations are unavailable in Phase 1")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must contain explicit hosts")
        if self.app_mode in {"VPS", "VERCEL"} and (
            not self.database_url.startswith("postgresql") or not self.cookie_secure
        ):
            raise ValueError(f"{self.app_mode} profile requires PostgreSQL and secure cookies")
        if self.worker_poll_seconds >= self.worker_lease_seconds:
            raise ValueError("Worker poll interval must be shorter than its lease")
        return self
