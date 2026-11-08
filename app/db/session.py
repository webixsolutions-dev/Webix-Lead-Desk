"""Database construction with explicitly enabled SQLite safeguards."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.core.config import Settings


@dataclass(frozen=True)
class Database:
    """Database engine and short-lived session factory."""

    engine: Engine
    sessions: sessionmaker[Session]


def configure_sqlite(connection: sqlite3.Connection, record: ConnectionPoolEntry) -> None:
    """Enable referential integrity, bounded lock waits and WAL on each connection."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_database(settings: Settings) -> Database:
    """Construct an engine without creating or modifying the schema."""
    url = make_url(settings.database_url)
    options: dict[str, object] = {}
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        options["check_same_thread"] = False
    engine = create_engine(url, connect_args=options, pool_pre_ping=True, hide_parameters=True)
    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", configure_sqlite)
    return Database(engine, sessionmaker(engine, expire_on_commit=False, autoflush=False))
