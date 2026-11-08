"""Typed application services shared by routes and workers."""

from dataclasses import dataclass

from app.core.config import Settings
from app.db.session import Database


@dataclass(frozen=True)
class Services:
    """References to the application's configured infrastructure."""

    settings: Settings
    db: Database
