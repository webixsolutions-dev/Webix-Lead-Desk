"""Weekly encrypted PC backups with durable receipts and scoped eight-file retention."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.backup_models import BackupStatus
from app.db.models import new_id
from app.db.session import Database

logger = logging.getLogger(__name__)


def record_success(db: Database, digest: str, file_bytes: int) -> None:
    """Record success only after the encrypted file is fully written and fsynced."""
    with db.sessions.begin() as session:
        row = session.get(BackupStatus, "primary")
        if row is None:
            row = BackupStatus(key="primary")
            session.add(row)
        row.last_success_at, row.sha256, row.file_bytes = utc_now(), digest, file_bytes
        row.error_code = ""


def reserve(db: Database, settings: Settings, now: datetime) -> tuple[str, str] | None:
    """Reserve one due backup; a crashed reservation expires after an hour."""
    with db.sessions.begin() as session:
        if session.get(BackupStatus, "primary") is None:
            try:
                with session.begin_nested():
                    session.add(BackupStatus(key="primary"))
                    session.flush()
            except IntegrityError:
                pass  # Concurrent initializer is resolved by the conditional update.
        claim = new_id()
        namespace = session.scalar(
            update(BackupStatus)
            .where(
                BackupStatus.key == "primary",
                or_(BackupStatus.lease_until.is_(None), BackupStatus.lease_until <= now),
                or_(
                    BackupStatus.last_success_at.is_(None),
                    BackupStatus.last_success_at
                    <= now - timedelta(days=settings.backup_interval_days),
                ),
            )
            .values(claim=claim, lease_until=now + timedelta(hours=1), last_attempt_at=now)
            .returning(BackupStatus.namespace)
        )
        return (claim, namespace) if namespace else None


def retain(directory: Path, namespace: str, keep: int) -> None:
    """Delete only older regular automatic files owned by this workspace, after success."""
    files = sorted(
        (
            p
            for p in directory.glob(f"wld-auto-{namespace}-*.wld")
            if p.is_file() and not p.is_symlink()
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    for path in files[keep:]:
        path.unlink()


def scheduled_backup(db: Database, settings: Settings, now: datetime) -> None:
    """Catch up once when the worker returns; never make a backup on Vercel."""
    if settings.app_mode == "VERCEL" or not settings.backup_directory or not settings.backup_key:
        return
    reservation = reserve(db, settings, now)
    if reservation is None:
        return
    claim, namespace = reservation
    try:
        from app.operations.backups import export_backup

        path = settings.backup_directory / (
            f"wld-auto-{namespace}-{now.strftime('%Y%m%dT%H%M%S')}-{claim}.wld"
        )
        export_backup(db, settings, path, online=True)
        retain(settings.backup_directory, namespace, settings.backup_keep)
    except (OSError, ValueError):
        with db.sessions.begin() as session:
            session.execute(
                update(BackupStatus)
                .where(BackupStatus.claim == claim)
                .values(error_code="backup_or_retention_failed")
            )
        logger.error(
            "Scheduled backup failed; check private backup configuration and disk capacity"
        )
        # Keep the one-hour retry backoff after failure.
        return
    with db.sessions.begin() as session:
        session.execute(
            update(BackupStatus)
            .where(BackupStatus.claim == claim)
            .values(lease_until=None, claim="")
        )
