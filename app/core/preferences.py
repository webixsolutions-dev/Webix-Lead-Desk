"""Validated, versioned, non-secret settings with optimistic conflict handling."""

from datetime import datetime
from zoneinfo import ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.timezones import zone
from app.db.models import AuditEvent, WorkspaceSetting
from app.db.session import Database


class Preferences(BaseModel):
    """The safe subset of settings editable in Phase 1."""

    model_config = ConfigDict(extra="forbid")
    workspace_name: str = Field(min_length=1, max_length=80)
    display_timezone: str
    sending_timezone: str
    preview_enabled: bool

    @field_validator("workspace_name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        """Reject whitespace-only names and normalize outer whitespace."""
        if not value.strip():
            raise ValueError("Workspace name is required")
        return value.strip()

    @field_validator("display_timezone", "sending_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Accept only an actual IANA timezone."""
        try:
            zone(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid IANA timezone") from error
        return value


class SettingsConflict(Exception):
    """The page's settings version was superseded by another edit."""


def get_preferences(db: Database, settings: Settings) -> tuple[Preferences, int]:
    """Read saved preferences, falling back to environment defaults without writing."""
    with db.sessions() as session:
        stored = session.get(WorkspaceSetting, "workspace")
        if stored is not None:
            return Preferences.model_validate(stored.value), stored.version
    return Preferences(
        workspace_name=settings.workspace_name,
        display_timezone=settings.display_timezone,
        sending_timezone=settings.sending_timezone,
        preview_enabled=settings.preview_enabled,
    ), 0


def save_preferences(
    db: Database, value: Preferences, version: int, actor: str, now: datetime
) -> None:
    """Save the exact expected version and a redacted audit event atomically."""
    try:
        with db.sessions.begin() as session:
            if version == 0:
                session.add(
                    WorkspaceSetting(
                        key="workspace", value=value.model_dump(), version=1, updated_at=now
                    )
                )
                session.flush()
            else:
                updated = session.scalar(
                    update(WorkspaceSetting)
                    .where(
                        WorkspaceSetting.key == "workspace",
                        WorkspaceSetting.version == version,
                    )
                    .values(value=value.model_dump(), version=version + 1, updated_at=now)
                    .returning(WorkspaceSetting.key)
                )
                if updated is None:
                    raise SettingsConflict("Settings changed in another tab. Reload and try again.")
            session.add(
                AuditEvent(
                    actor_id=actor,
                    action="settings.updated",
                    entity_id="workspace",
                    details={"fields": sorted(value.model_fields_set), "version": version + 1},
                )
            )
    except IntegrityError as error:
        raise SettingsConflict("Settings changed in another tab. Reload and try again.") from error
