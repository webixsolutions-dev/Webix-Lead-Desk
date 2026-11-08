"""Offline SQLite-to-PostgreSQL transfer with the source left paused and no data overwrite."""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.core.config import Settings
from app.db.migrate import migrate
from app.db.session import create_database
from app.operations.backups import export_backup, require_empty_target, restore_backup


def load_private_settings(path: Path) -> Settings:
    """Load an explicit private file, refusing incomplete connection/key selection."""
    values: dict[str, Any] = {
        key.lower(): value for key, value in dotenv_values(path).items() if value is not None
    }
    if not all(values.get(key) for key in ["database_url", "session_secret", "backup_key"]):
        raise ValueError("Private environment files require database, session and backup keys")
    if "allowed_hosts" in values:
        values["allowed_hosts"] = json.loads(values["allowed_hosts"])
    return Settings(_env_file=None, **values)


def main() -> None:
    """Transfer only between the requested profiles after an explicit offline confirmation."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--target-env", type=Path, required=True)
    parser.add_argument("--backup-file", type=Path, required=True)
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args()
    source_db = target_db = None
    try:
        source = load_private_settings(args.source_env)
        target = load_private_settings(args.target_env)
        if not source.database_url.startswith("sqlite") or not target.database_url.startswith(
            "postgresql"
        ):
            raise ValueError("Use a SQLite source and a PostgreSQL target")
        if (
            source.backup_key != target.backup_key
            or source.encryption_key != target.encryption_key
            or target.live_send_enabled
        ):
            raise ValueError(
                "Use matching private backup/credential keys and disable target sending"
            )
        target_db = create_database(target)
        require_empty_target(target_db)
        source_db = create_database(source)
        export_backup(source_db, source, args.backup_file, offline_confirmed=args.offline_confirmed)
        migrate(target)
        counts = restore_backup(
            target_db, target, args.backup_file, offline_confirmed=args.offline_confirmed
        )
        logging.info(
            "Transferred and verified %s rows. Both databases remain in maintenance.",
            sum(counts.values()),
        )
    except Exception:
        logging.error(
            "Transfer stopped; check private profiles, empty target, maintenance and leases. "
            "No credentials logged."
        )
        raise SystemExit(2) from None
    finally:
        if source_db:
            source_db.engine.dispose()
        if target_db:
            target_db.engine.dispose()


if __name__ == "__main__":
    main()
