"""Cached owner-only PostgreSQL security/capacity diagnostics and durable backup status."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.backup_models import BackupStatus
from app.db.models import Base, WorkerInstance
from app.db.session import Database


def inspect_postgres(db: Database) -> dict[str, Any]:
    """Read only application catalog names; never expose connection strings or row contents."""
    names = set(Base.metadata.tables) | {"alembic_version"}
    with db.engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
            SELECT c.relname, c.relrowsecurity,
                   EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid) AS policies,
                   pg_total_relation_size(c.oid) AS bytes
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p')
        """)
            )
            .mappings()
            .all()
        )
        size = connection.scalar(text("SELECT pg_database_size(current_database())")) or 0
    application = [dict(row) for row in rows if row["relname"] in names]
    return {
        "bytes": int(size),
        "unprotected": sorted(row["relname"] for row in application if not row["relrowsecurity"]),
        "policies": sorted(row["relname"] for row in application if row["policies"]),
        "largest": sorted(application, key=lambda row: row["bytes"], reverse=True)[:8],
        "error": False,
    }


@dataclass
class HostedMonitor:
    """Bound catalog queries per web instance; startup and later page reads refresh drift."""

    checked_at: datetime | None = None
    value: dict[str, Any] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def refresh(self, db: Database, settings: Settings, now: datetime) -> dict[str, Any]:
        """Return a short cached snapshot, reporting unavailable checks without raw errors."""
        if db.engine.dialect.name != "postgresql":
            return {}
        with self.lock:
            if self.checked_at and now < self.checked_at + timedelta(
                seconds=settings.hosted_status_cache_seconds
            ):
                return self.value
            try:
                self.value = inspect_postgres(db)
            except SQLAlchemyError:
                self.value = {"error": True}
            self.checked_at = now
            return self.value


def dashboard_status(
    db: Database, settings: Settings, monitor: HostedMonitor, now: datetime
) -> dict[str, Any]:
    """Combine observed infrastructure status without claiming to measure provider inactivity."""
    stats = monitor.refresh(db, settings, now)
    with db.sessions() as session:
        backup = session.get(BackupStatus, "primary")
        worker = session.get(WorkerInstance, "primary")
    warnings: list[str] = []
    if stats.get("error"):
        warnings.append("Database protection and size checks are unavailable; review connectivity.")
    if stats.get("unprotected"):
        warnings.append("RLS is disabled on application tables: " + ", ".join(stats["unprotected"]))
    if stats.get("policies"):
        warnings.append("Unexpected RLS policies need review: " + ", ".join(stats["policies"]))
    if stats.get("bytes", 0) >= settings.database_warning_bytes:
        warnings.append(
            "Database size has reached the configured warning threshold (default 400 MB)."
        )
    last = backup.last_success_at if backup else None
    if not last:
        warnings.append(
            "No successful encrypted backup is recorded. Configure the PC backup folder and key."
        )
    elif now - last > timedelta(days=8):
        warnings.append(
            "The last encrypted backup is older than 8 days. "
            "Start the PC worker and check its folder."
        )
    if backup and backup.error_code:
        warnings.append(
            "The latest automatic backup or retention failed; "
            "check PC storage and private settings."
        )
    if not worker:
        warnings.append("No worker connection recorded. Start the PC worker using this database.")
    elif now - worker.heartbeat_at >= timedelta(days=5):
        warnings.append(
            "Worker has not connected for 5 days. Check Supabase project activity "
            "and start the PC; this does not measure the provider's inactivity timer."
        )
    return {
        "warnings": warnings,
        "backup_at": last,
        "size_warning": stats.get("bytes", 0) >= settings.database_warning_bytes,
        "stats": stats,
        "checked_at": monitor.checked_at,
    }
