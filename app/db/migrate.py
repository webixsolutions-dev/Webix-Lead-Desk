"""Programmatic access to the same Alembic migrations used by the CLI."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.db.session import create_database


def migrate(settings: Settings, revision: str = "head") -> None:
    """Upgrade the configured database using versioned migration files only."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    db = create_database(settings)
    try:
        with db.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    finally:
        db.engine.dispose()
