"""Typed application services shared by routes and workers."""

from dataclasses import dataclass, field

from app.core.config import Settings
from app.db.session import Database
from app.operations.hosted import HostedMonitor


@dataclass(frozen=True)
class Services:
    """References to the application's configured infrastructure."""

    settings: Settings
    db: Database
    monitor: HostedMonitor = field(default_factory=HostedMonitor)
