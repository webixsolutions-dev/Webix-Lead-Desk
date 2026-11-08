"""Programmatic access to the same Alembic migrations used by the CLI."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

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


def migration_head() -> str | None:
    """Read the versioned schema head without connecting to a database."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()
