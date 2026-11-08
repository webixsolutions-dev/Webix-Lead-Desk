"""Bounded, portable snapshots of known SQLAlchemy tables; no environment secrets or sessions."""

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, select, text
from sqlalchemy.engine import Connection

from app.core.config import Settings
from app.db.migrate import migration_head
from app.db.models import Base, UTCDateTime
from app.db.session import Database

OMIT_SESSION_TABLES = {"sessions", "login_throttles"}


def canonical(value: Any) -> bytes:
    """Encode deterministic UTF-8 JSON for snapshot hashes and encrypted payloads."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_value
    ).encode()


def _json_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unsupported snapshot value")


def key_fingerprint(settings: Settings) -> str:
    """Identify the credential key without exporting it or exposing its bytes."""
    return (
        hashlib.sha256(settings.encryption_key.get_secret_value().encode()).hexdigest()
        if settings.encryption_key
        else ""
    )


def table_rows(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    """Read known tables in stable primary-key order, omitting transferable login sessions."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in Base.metadata.sorted_tables:
        rows = []
        if table.name not in OMIT_SESSION_TABLES:
            for row in connection.execute(
                select(table).order_by(*table.primary_key.columns)
            ).mappings():
                rows.append(
                    {
                        name: value.isoformat() if isinstance(value, datetime) else value
                        for name, value in row.items()
                    }
                )
        # Database collations can order text primary keys differently across backends.
        keys = [column.name for column in table.primary_key.columns]
        tables[table.name] = sorted(rows, key=lambda row: canonical([row[key] for key in keys]))
    return tables


def snapshot(db: Database, settings: Settings) -> dict[str, Any]:
    """Take one consistent SQLite or PostgreSQL read snapshot after the maintenance fence."""
    with db.engine.connect() as connection:
        if db.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        else:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            connection.begin()
        try:
            if (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                != migration_head()
            ):
                raise ValueError("Migrate to the current schema before backup")
            tables = table_rows(connection)
            payload = {
                "format": 1,
                "schema": migration_head(),
                "backend": db.engine.dialect.name,
                "tables": tables,
                "counts": {name: len(rows) for name, rows in tables.items()},
                "credential_key": key_fingerprint(settings)
                if tables["integration_secrets"]
                else "",
            }
            payload["data_sha256"] = hashlib.sha256(canonical(tables)).hexdigest()
            return payload
        finally:
            connection.rollback()


def validate_payload(payload: Any, settings: Settings) -> dict[str, list[dict[str, Any]]]:
    """Validate the current known schema and every row before inserting any restored data."""
    if (
        not isinstance(payload, dict)
        or payload.get("format") != 1
        or payload.get("schema") != migration_head()
    ):
        raise ValueError("Backup format or schema differs; use the matching application release")
    tables = payload.get("tables")
    expected = {table.name: table for table in Base.metadata.sorted_tables}
    if not isinstance(tables, dict) or set(tables) != set(expected):
        raise ValueError("Backup table manifest is invalid")
    if payload.get("data_sha256") != hashlib.sha256(canonical(tables)).hexdigest():
        raise ValueError("Backup data checksum is invalid")
    if payload.get("credential_key") and payload["credential_key"] != key_fingerprint(settings):
        raise ValueError(
            "Configure the original ENCRYPTION_KEY privately before restoring credentials"
        )
    if payload.get("counts") != {
        name: len(rows) for name, rows in tables.items() if isinstance(rows, list)
    }:
        raise ValueError("Backup row counts are invalid")
    result: dict[str, list[dict[str, Any]]] = {}
    for name, table in expected.items():
        if not isinstance(tables[name], list):
            raise ValueError("Backup rows are invalid")
        restored = []
        for row in tables[name]:
            if not isinstance(row, dict) or set(row) != set(table.columns.keys()):
                raise ValueError("Backup columns are invalid")
            values = dict(row)
            for column in table.columns:
                value = values[column.name]
                if value is not None and isinstance(column.type, (DateTime, UTCDateTime)):
                    try:
                        values[column.name] = datetime.fromisoformat(value)
                        if values[column.name].tzinfo is None:
                            raise ValueError("Timezone missing")
                    except (TypeError, ValueError) as error:
                        raise ValueError("Backup timestamp is invalid") from error
            restored.append(values)
        if name in OMIT_SESSION_TABLES and restored:
            raise ValueError("Transferable login sessions are not permitted")
        result[name] = restored
    control = result["runtime_control"]
    if len(control) != 1 or control[0]["key"] != "primary" or control[0]["maintenance"] is not True:
        raise ValueError("Backup must retain the maintenance gate")
    return result
