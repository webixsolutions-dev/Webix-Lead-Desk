"""Versioned owner configuration; credentials live only in the encrypted vault."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.core.preferences import SettingsConflict
from app.db.models import AuditEvent, WorkspaceSetting
from app.db.session import Database


class SearchConfig(BaseModel):
    """Explicit provider limits; zero prices/caps prevent paid requests."""

    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai_web_search", "google_places"] = "openai_web_search"
    enabled: bool = True
    discovery_batch_size: int = Field(default=5, ge=1, le=10)
    daily_calls: int = Field(default=60, ge=0, le=1000)
    monthly_calls: int = Field(default=0, ge=0, le=30000)
    monthly_usd_micro: int = Field(default=0, ge=0, le=1_000_000_000)
    search_usd_micro: int = Field(default=0, ge=0, le=1_000_000)
    places_enabled: bool = False
    places_daily_calls: int = Field(default=0, ge=0, le=1000)
    places_monthly_calls: int = Field(default=0, ge=0, le=30000)
    places_monthly_usd_micro: int = Field(default=0, ge=0, le=1_000_000_000)
    places_search_usd_micro: int = Field(default=0, ge=0, le=1_000_000)
    no_website_offer_id: str = Field(default="", max_length=36)


def get_config(db: Database) -> tuple[SearchConfig, int]:
    """Load safe defaults without silently enabling funding or external credentials."""
    with db.sessions() as session:
        value = session.get(WorkspaceSetting, "acquisition")
        return (
            (SearchConfig.model_validate(value.value), value.version)
            if value
            else (SearchConfig(), 0)
        )


def save_config(db: Database, value: SearchConfig, version: int, actor: str) -> None:
    """Record a complete immutable configuration revision in the audit log."""
    try:
        with db.sessions.begin() as session:
            if version == 0:
                session.add(
                    WorkspaceSetting(key="acquisition", value=value.model_dump(), version=1)
                )
                session.flush()
            elif not session.scalar(
                update(WorkspaceSetting)
                .where(WorkspaceSetting.key == "acquisition", WorkspaceSetting.version == version)
                .values(value=value.model_dump(), version=version + 1, updated_at=utc_now())
                .returning(WorkspaceSetting.key)
            ):
                raise SettingsConflict("Acquisition settings changed; reload before saving")
            session.add(
                AuditEvent(
                    actor_id=actor,
                    action="acquisition.settings",
                    entity_id="acquisition",
                    details={"version": version + 1, "value": value.model_dump()},
                )
            )
    except IntegrityError as error:
        raise SettingsConflict("Acquisition settings changed; reload before saving") from error
