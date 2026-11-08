"""Offline SQLite-to-PostgreSQL transfer against a disposable local CI database."""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.vault import read_secret
from app.db.delivery_models import SendIntent
from app.db.migrate import migrate
from app.db.outreach_models import DraftApproval
from app.db.session import Database, create_database
from app.jobs.queue import finish_job, release_worker
from app.operations.backups import export_backup, require_empty_target, restore_backup
from app.operations.control import maintenance_enabled, set_maintenance
from app.operations.portable import canonical, snapshot
from app.policy.contacts import suppress
from app.sending.service import dispatch
from tests.delivery_helpers import NOW, FakeTransport, approved_intent, claim_send
from tests.delivery_helpers import delivery_setup as delivery_setup
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_operations import backup_config
from tests.test_research import research_company as research_company


@pytest.mark.postgres
def test_sqlite_to_postgres_keeps_every_row_and_delivery_hold(
    db: Database, delivery_setup: Settings, drafting_setup: dict[str, str], tmp_path: Path
) -> None:
    """Transfer real mapped rows, then compare all content and encrypted mailbox recovery."""
    url_text = os.environ.get("TEST_DATABASE_URL")
    if not url_text:
        pytest.skip("Set TEST_DATABASE_URL for the isolated local PostgreSQL rehearsal")
    url = make_url(url_text)
    assert url.host in {"localhost", "127.0.0.1"}
    assert url.database == "lead_desk_test", "Never clean up another database"
    config = backup_config(delivery_setup)
    item = approved_intent(db, drafting_setup)
    job = claim_send(db, config, item)
    dispatch(db, config, job, transport=FakeTransport(outcome="raise"), clock=lambda: NOW)
    finish_job(db, job, NOW, result="synthetic uncertain delivery")
    release_worker(db, "test-worker", utc_now() - timedelta(seconds=1))
    suppress(db, "service@example.com", "Synthetic suppression retained after transfer")
    set_maintenance(db, True, "Offline cross-database transfer rehearsal")
    path = tmp_path / "transfer.wld"
    export_backup(db, config, path, offline_confirmed=True)
    target_config = config.model_copy(update={"database_url": url_text, "live_send_enabled": False})
    migrate(target_config)
    target = create_database(target_config)
    # Refuse pre-existing rows, even on the explicitly named test database.
    require_empty_target(target)
    try:
        restore_backup(target, target_config, path, offline_confirmed=True)
        original = snapshot(db, config)
        restored = snapshot(target, target_config)
        assert original["data_sha256"] == restored["data_sha256"]
        assert canonical(original["tables"]) == canonical(restored["tables"])
        with target.sessions() as session:
            saved = session.get(SendIntent, item.id)
            assert saved and saved.state == "uncertain" and saved.message_id == item.message_id
            assert session.scalar(select(DraftApproval)) is not None
            assert maintenance_enabled(session)
        assert read_secret(target, target_config, "mailbox:" + item.mailbox_id) == (
            "offline-only-password"
        )
    finally:
        migration = Config()
        migration.set_main_option(
            "script_location", str(Path(__file__).parents[1] / "app/db/migrations")
        )
        with target.engine.begin() as connection:
            migration.attributes["connection"] = connection
            command.downgrade(migration, "base")
        target.engine.dispose()
        migrate(target_config)
