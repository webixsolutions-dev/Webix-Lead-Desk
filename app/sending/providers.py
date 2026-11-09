"""Provider presets and per-mailbox configuration, with no arbitrary mail hosts."""

from typing import Any

from app.core.config import Settings
from app.db.outreach_models import Mailbox

PRESETS: dict[str, dict[str, Any]] = {
    "titan": {
        "smtp_host": "smtp.titan.email",
        "smtp_port": 465,
        "imap_host": "imap.titan.email",
        "imap_port": 993,
        "daily": 300,
        "hourly": 50,
    },
    "hostinger": {
        "smtp_host": "smtp.hostinger.com",
        "smtp_port": 465,
        "imap_host": "imap.hostinger.com",
        "imap_port": 993,
        "daily": 300,
        "hourly": 30,
    },
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "daily": 20,
        "hourly": 10,
    },
}
PERSONAL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
}
DOMAIN_READINESS = ("spf", "dkim", "dmarc", "privacy", "provider_limits", "test_accepted")


def readiness_fields(mailbox: Mailbox) -> tuple[str, ...]:
    """Use Google-managed authentication only for its personal mail domains."""
    if mailbox.provider == "gmail" and mailbox.email.rsplit("@", 1)[-1] in {
        "gmail.com",
        "googlemail.com",
    }:
        return ("app_password", "privacy", "provider_limits", "test_accepted")
    return DOMAIN_READINESS


def connection(provider: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Accept only provider-correct hostnames and SMTP SSL/STARTTLS or IMAP SSL ports."""
    if provider not in PRESETS:
        raise ValueError("Choose Titan, Hostinger or Gmail")
    values = {k: v for k, v in PRESETS[provider].items() if k not in {"daily", "hourly"}}
    for key, value in (overrides or {}).items():
        if key not in values:
            raise ValueError("Unsupported mail setting")
        if value not in {None, ""}:
            values[key] = int(value) if key.endswith("port") else str(value).strip().lower()
    if (
        values["smtp_host"] != PRESETS[provider]["smtp_host"]
        or values["imap_host"] != PRESETS[provider]["imap_host"]
        or values["smtp_port"] not in {465, 587}
        or values["imap_port"] != 993
    ):
        raise ValueError("Use the provider's public hostname, SMTP 465/587 and IMAP 993")
    return values


def mailbox_settings(settings: Settings, mailbox: Mailbox) -> Settings:
    """Select this mailbox's validated endpoints, without changing global configuration."""
    values = connection(mailbox.provider, mailbox.connection)
    return settings.model_copy(
        update={"mail_provider": mailbox.provider, **{"mail_" + k: v for k, v in values.items()}}
    )
