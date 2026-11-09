"""Versioned private desktop configuration, encrypted before it reaches disk."""

import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.desktop.platform import protect


class DesktopConfig(BaseModel):
    """Only desktop setup fields; sending and paid integrations remain disabled."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    version: int = 1
    database_url: SecretStr
    encryption_key: SecretStr
    session_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(48)))
    backup_key: SecretStr = Field(default_factory=lambda: SecretStr(Fernet.generate_key().decode()))
    backup_directory: str
    dashboard_url: str = "https://webix-lead-desk.vercel.app"
    autostart: bool = False

    def settings(self, root: Path) -> Settings:
        """Validate all user input before saving, opening a URL or starting a worker."""
        url = make_url(self.database_url.get_secret_value())
        if self.version != 1 or url.drivername != "postgresql+psycopg":
            raise ValueError("Use a PostgreSQL psycopg connection")
        if url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("Encrypted PostgreSQL connection required")
        if not self.encryption_key.get_secret_value() or not self.backup_key.get_secret_value():
            raise ValueError("Encryption and backup keys are required")
        if not Path(self.backup_directory).is_absolute():
            raise ValueError("Select an absolute backup folder")
        return Settings(
            _env_file=None,
            app_mode="PC",
            database_url=self.database_url.get_secret_value(),
            encryption_key=self.encryption_key,
            session_secret=self.session_secret,
            backup_key=self.backup_key,
            backup_directory=Path(self.backup_directory),
            data_dir=root / "data",
            public_base_url=self.dashboard_url,
            preview_enabled=False,
            live_send_enabled=False,
            mailbox_tests_enabled=True,
            external_integrations_enabled=False,
            openai_enabled=False,
            linkedin_posting_enabled=False,
            pc_keep_awake=True,
            cookie_secure=True,
            allowed_hosts=["127.0.0.1", "localhost"],
            database_prepare_statements=True,
            backup_keep=8,
            backup_interval_days=7,
        )


def save_config(root: Path, config: DesktopConfig) -> None:
    """Atomically replace only an encrypted blob; preserve the last file on failure."""
    config.settings(root)
    payload = config.model_dump()
    for key in ("database_url", "encryption_key", "session_secret", "backup_key"):
        payload[key] = payload[key].get_secret_value()
    encrypted = protect(json.dumps(payload).encode())
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ("settings-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(root / "settings.dat")
    finally:
        temporary.unlink(missing_ok=True)


def load_config(root: Path) -> DesktopConfig | None:
    """Unlock existing settings; never silently replace damaged or foreign keys."""
    path = root / "settings.dat"
    if not path.exists():
        return None
    config = DesktopConfig.model_validate_json(protect(path.read_bytes(), decrypt=True))
    config.settings(root)
    return config
