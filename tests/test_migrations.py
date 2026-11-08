"""Portable migrations and schema integrity checks."""

import os
import secrets
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.migrate import migrate
from app.db.models import Base, Job
from app.db.session import Database, create_database
from app.jobs.queue import enqueue_health


def test_schema_matches_models_and_foreign_keys_enabled(db: Database) -> None:
    """Alembic creates exactly the mapped Phase 1 schema with active SQLite FKs."""
    with db.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
    assert "companies" in inspect(db.engine).get_table_names()


def test_reapplying_migrations_preserves_jobs(db: Database, settings: Settings) -> None:
    """Idempotent upgrade preserves durable work across repeated startup preparation."""
    from app.core.clock import utc_now

    saved = enqueue_health(db, "persist", utc_now())
    migrate(settings)
    with db.sessions() as session:
        assert session.scalar(select(Job.id)) == saved.id


@pytest.mark.postgres
def test_postgres_migrations_roundtrip() -> None:
    """Verify the same migrations on an explicitly isolated local PostgreSQL database."""
    url_text = os.environ.get("TEST_DATABASE_URL")
    if not url_text:
        pytest.skip("Set TEST_DATABASE_URL for the local ephemeral PostgreSQL test")
    url = make_url(url_text)
    assert url.host in {"localhost", "127.0.0.1"}, "Only a local test database is allowed"
    assert url.database == "lead_desk_test", (
        "Never run destructive schema tests on another database"
    )
    settings = Settings(
        _env_file=None, database_url=url_text, session_secret=SecretStr(secrets.token_urlsafe(48))
    )
    migrate(settings)
    db = create_database(settings)
    try:
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parents[1] / "app/db/migrations")
        )
        with db.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        assert "users" not in inspect(db.engine).get_table_names()
        migrate(settings)
        assert "users" in inspect(db.engine).get_table_names()
    finally:
        db.engine.dispose()
