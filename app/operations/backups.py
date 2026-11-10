"""Authenticated encrypted backup files and transactional empty-target restoration."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, inspect, select, text

from app.core.config import Settings
from app.db.migrate import migration_head
from app.db.models import Base
from app.db.session import Database
from app.operations.control import quiesce
from app.operations.portable import canonical, snapshot, table_rows, validate_payload


def cipher(settings: Settings) -> Fernet:
    """Require a dedicated environment-held backup key, never a key embedded in the file."""
    if not settings.backup_key:
        raise ValueError("Set a private BACKUP_KEY before backup or restore")
    return Fernet(settings.backup_key.get_secret_value().encode())


def export_backup(
    db: Database,
    settings: Settings,
    output: Path,
    *,
    offline_confirmed: bool = False,
    online: bool = False,
) -> dict[str, Any]:
    """Write one new encrypted snapshot without overwriting an existing backup."""
    encryption = cipher(settings)
    if settings.app_mode == "VERCEL":
        raise ValueError("Backups must run on the PC or VPS worker")
    if not online:
        quiesce(db, offline_confirmed=offline_confirmed)
    payload = snapshot(db, settings)
    if online:
        from app.operations.recovery_snapshot import fence_snapshot

        fence_snapshot(payload)
    secrets = [row["ciphertext"] for row in payload["tables"]["integration_secrets"]]
    secrets.extend(
        row["encrypted_secret"] for row in payload["tables"]["owner_mfa"] if row["encrypted_secret"]
    )
    if secrets:
        if not settings.encryption_key:
            raise ValueError("Configure the original ENCRYPTION_KEY before backing up credentials")
        credential_cipher = Fernet(settings.encryption_key.get_secret_value().encode())
        try:
            for secret in secrets:
                credential_cipher.decrypt(secret.encode())
        except InvalidToken as error:
            raise ValueError(
                "The configured ENCRYPTION_KEY cannot open stored credentials"
            ) from error
    raw = canonical(payload)
    if len(raw) > settings.backup_max_bytes:
        raise ValueError("Backup exceeds BACKUP_MAX_BYTES; use a reviewed larger limit")
    encrypted = encryption.encrypt(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError("Backup output exists; choose a new filename") from error
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encrypted)
            destination.flush()
            os.fsync(destination.fileno())
    except OSError:
        output.unlink(missing_ok=True)
        raise
    from app.operations.scheduled_backup import record_success

    digest = hashlib.sha256(encrypted).hexdigest()
    record_success(db, digest, len(encrypted))
    return {
        "schema": payload["schema"],
        "rows": sum(payload["counts"].values()),
        "file_sha256": hashlib.sha256(encrypted).hexdigest(),
    }


def read_backup(settings: Settings, source: Path) -> dict[str, list[dict[str, Any]]]:
    """Read a bounded authenticated payload, failing before writes on wrong keys or corruption."""
    with source.open("rb") as stream:
        encrypted = stream.read(settings.backup_max_bytes * 2 + 1)
    if len(encrypted) > settings.backup_max_bytes * 2:
        raise ValueError("Encrypted backup exceeds the configured size limit")
    try:
        raw = cipher(settings).decrypt(encrypted)
        if len(raw) > settings.backup_max_bytes:
            raise ValueError("Decrypted backup exceeds the configured size limit")
        payload = json.loads(raw)
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Backup could not be authenticated; verify its key and file") from error
    return validate_payload(payload, settings)


def require_empty_target(db: Database) -> None:
    """Reject unrelated schemas or existing records before any migration or restore writes."""
    known = {table.name: table for table in Base.metadata.sorted_tables}
    names = set(inspect(db.engine).get_table_names())
    if names - (set(known) | {"alembic_version"}):
        raise ValueError("Target contains unrelated tables; choose an empty application database")
    with db.engine.connect() as connection:
        for name in names & set(known):
            if connection.scalar(select(func.count()).select_from(known[name])):
                raise ValueError("Restore target contains data; no existing records were changed")


def restore_backup(
    db: Database, settings: Settings, source: Path, *, offline_confirmed: bool = False
) -> dict[str, int]:
    """Restore only an empty current-schema target, with sending off and maintenance retained."""
    if not offline_confirmed or settings.live_send_enabled:
        raise ValueError("Stop target processes and disable LIVE_SEND_ENABLED before restore")
    tables = read_backup(settings, source)
    require_empty_target(db)
    with db.engine.begin() as connection:
        if connection.scalar(text("SELECT version_num FROM alembic_version")) != migration_head():
            raise ValueError("Migrate the empty target to the current schema before restore")
        if db.engine.dialect.name == "postgresql":
            # Lock out inserts while checking and restoring the empty target.
            names = ", ".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
            connection.exec_driver_sql("LOCK TABLE " + names + " IN ACCESS EXCLUSIVE MODE")
        elif db.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        for table in Base.metadata.sorted_tables:
            if connection.scalar(select(func.count()).select_from(table)):
                raise ValueError("Restore target contains data; no existing records were changed")
        for table in Base.metadata.sorted_tables:
            rows = tables[table.name]
            for index in range(0, len(rows), 250):
                connection.execute(table.insert(), rows[index : index + 250])
        counts = {
            table.name: connection.scalar(select(func.count()).select_from(table)) or 0
            for table in Base.metadata.sorted_tables
        }
        if counts != {name: len(rows) for name, rows in tables.items()}:
            raise ValueError("Restored row counts differ; transaction was rolled back")
        if canonical(table_rows(connection)) != canonical(tables):
            raise ValueError("Restored content differs; transaction was rolled back")
    return counts
