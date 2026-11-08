"""Alembic entry point for CLI and programmatic migrations."""

from alembic import context
from sqlalchemy.engine import Connection

from app.core.config import Settings
from app.db.models import Base
from app.db.session import create_database


def run_with_connection(connection: Connection) -> None:
    """Run migrations against an existing connection."""
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    """Select offline SQL generation or a real configured connection."""
    if context.is_offline_mode():
        context.configure(
            url=Settings().database_url,
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    existing = context.config.attributes.get("connection")
    if isinstance(existing, Connection):
        run_with_connection(existing)
        return
    db = create_database(Settings())
    try:
        with db.engine.connect() as connection:
            run_with_connection(connection)
    finally:
        db.engine.dispose()


run_migrations()
