"""Supabase-compatible PostgreSQL catalog and dropped-connection checks on CI loopback only."""

import os
import secrets
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.db.migrate import migrate
from app.db.models import Base
from app.db.session import Database, create_database
from app.operations.hosted import inspect_postgres


@pytest.fixture
def postgres_db() -> Iterator[Database]:
    """Use only the explicitly named disposable local PostgreSQL database."""
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL for the local ephemeral PostgreSQL test")
    url = make_url(value)
    assert url.host in {"127.0.0.1", "localhost"} and url.database == "lead_desk_test"
    settings = Settings(
        _env_file=None,
        database_url=value,
        database_prepare_statements=False,
        session_secret=SecretStr(secrets.token_urlsafe(48)),
    )
    migrate(settings)
    db = create_database(settings)
    try:
        yield db
    finally:
        db.engine.dispose()


@pytest.mark.postgres
def test_all_application_tables_default_deny_rls(postgres_db: Database) -> None:
    """An ordinary granted role sees zero rows and cannot insert; the owner still works."""
    db = postgres_db
    stats = inspect_postgres(db)
    assert stats["unprotected"] == [] and stats["policies"] == []
    with db.engine.begin() as conn:
        names = set(
            conn.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity")
            )
        )
        assert set(Base.metadata.tables) | {"alembic_version"} <= names
        conn.execute(text("CREATE ROLE wld_rls_probe NOLOGIN"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO wld_rls_probe"))
        conn.execute(text("GRANT SELECT, INSERT ON public.alembic_version TO wld_rls_probe"))
        assert conn.scalar(text("SELECT count(*) FROM public.alembic_version")) == 1
    try:
        with db.engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE wld_rls_probe"))
            assert conn.scalar(text("SELECT count(*) FROM public.alembic_version")) == 0
            with pytest.raises(DBAPIError):
                conn.execute(text("INSERT INTO public.alembic_version VALUES ('rls-denied')"))
            conn.rollback()
    finally:
        with db.engine.begin() as conn:
            conn.execute(text("DROP OWNED BY wld_rls_probe"))
            conn.execute(text("DROP ROLE wld_rls_probe"))


@pytest.mark.postgres
def test_dropped_connection_recovers_without_replaying_transaction(postgres_db: Database) -> None:
    """An interrupted transaction fails; a fresh checkout succeeds with prepares disabled."""
    db = postgres_db
    with db.engine.connect() as victim:
        driver = victim.connection.driver_connection
        assert driver is not None and driver.prepare_threshold is None
        pid = victim.scalar(text("SELECT pg_backend_pid()"))
        with db.engine.connect() as killer:
            killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with pytest.raises(DBAPIError):
            victim.scalar(text("SELECT 1"))
    with db.engine.connect() as recovered:
        assert recovered.scalar(text("SELECT 1")) == 1
    # Test a stale idle connection: pre-ping replaces it on checkout.
    with db.engine.connect() as idle:
        idle_pid = idle.scalar(text("SELECT pg_backend_pid()"))
    admin = create_database(
        Settings(
            _env_file=None,
            database_url=os.environ["TEST_DATABASE_URL"],
            session_secret=SecretStr(secrets.token_urlsafe(48)),
        )
    )
    try:
        with admin.engine.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": idle_pid})
        with db.engine.connect() as recovered:
            assert recovered.scalar(text("SELECT 1")) == 1
    finally:
        admin.engine.dispose()
