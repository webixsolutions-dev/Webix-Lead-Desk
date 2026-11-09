"""Local administration commands; never accept passwords as command arguments."""

import argparse
import getpass
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.auth import create_user, reset_password
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.migrate import migrate
from app.db.session import create_database
from app.jobs.queue import enqueue_health
from app.jobs.runner import run_worker
from app.operations.backups import export_backup, read_backup, restore_backup
from app.operations.control import set_maintenance

logger = logging.getLogger(__name__)


def confirmed_password() -> str:
    """Read and confirm a password through a hidden local terminal prompt."""
    password = getpass.getpass("Password (12–256 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    return password


def parser() -> argparse.ArgumentParser:
    """Define explicit local administration, recovery and worker commands."""
    root = argparse.ArgumentParser(description="Webix Lead Desk local administration")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply Alembic migrations")
    account = commands.add_parser("create-user", help="Create an owner or reviewer locally")
    account.add_argument("--email", required=True)
    account.add_argument("--name", required=True)
    account.add_argument("--role", choices=["owner", "reviewer"], default="owner")
    reset = commands.add_parser("reset-password", help="Reset a password and revoke sessions")
    reset.add_argument("--email", required=True)
    worker = commands.add_parser("worker", help="Run the separate background worker")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("enqueue-health", help="Queue one harmless worker check")
    commands.add_parser("check", help="Check the database connection")
    maintenance = commands.add_parser(
        "maintenance", help="Stop or resume application writes and worker claims"
    )
    maintenance.add_argument("state", choices=["on", "off"])
    maintenance.add_argument("--note", required=True)
    for name in ["backup", "restore", "verify-backup"]:
        operation = commands.add_parser(name, help="Private encrypted database " + name)
        operation.add_argument("--file", required=True, type=Path)
        if name == "backup":
            operation.add_argument(
                "--online",
                action="store_true",
                help="Consistent snapshot; restore holds all unfinished work",
            )
        if name != "verify-backup":
            operation.add_argument("--offline-confirmed", action="store_true")
    return root


def main() -> int:
    """Run a local command and report redacted errors through logging."""
    configure_logging()
    args = parser().parse_args()
    try:
        settings = Settings()
    except ValueError:
        logger.error(
            "Invalid configuration. Check .env against .env.example; no values were logged."
        )
        return 2
    if args.command == "migrate":
        try:
            migrate(settings)
        except SQLAlchemyError:
            logger.error("Migration failed. Check the database connection and migration history.")
            return 2
        logger.info("Database migration complete")
        return 0
    db = create_database(settings)
    try:
        match args.command:
            case "maintenance":
                set_maintenance(db, args.state == "on", args.note)
                logger.info(
                    "Maintenance %s; existing processes must be stopped for transfer", args.state
                )
            case "backup":
                result = export_backup(
                    db,
                    settings,
                    args.file,
                    offline_confirmed=args.offline_confirmed,
                    online=args.online,
                )
                logger.info(
                    "Encrypted backup saved: %s rows; file SHA-256 %s",
                    result["rows"],
                    result["file_sha256"],
                )
            case "verify-backup":
                tables = read_backup(settings, args.file)
                logger.info(
                    "Backup authenticated: %s rows; no data restored",
                    sum(len(rows) for rows in tables.values()),
                )
            case "restore":
                counts = restore_backup(
                    db, settings, args.file, offline_confirmed=args.offline_confirmed
                )
                logger.info(
                    "Restored and verified %s rows; maintenance remains enabled",
                    sum(counts.values()),
                )
            case "create-user":
                create_user(db, args.email, args.name, confirmed_password(), args.role)
                logger.info("Account created")
            case "reset-password":
                reset_password(db, args.email, confirmed_password())
                logger.info("Password reset; previous sessions revoked")
            case "worker":
                run_worker(db, settings, once=args.once)
            case "enqueue-health":
                now = utc_now()
                enqueue_health(db, f"health:{now.strftime('%Y%m%d%H%M')}", now)
                logger.info("Health check queued")
            case "check":
                with db.engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                logger.info("Database connection healthy")
    except IntegrityError:
        logger.error("The operation conflicts with an existing record. No data was overwritten.")
        return 2
    except SQLAlchemyError:
        logger.error("Database operation failed. Check connectivity and run migrations.")
        return 2
    except OSError:
        logger.error("Local backup I/O failed. Check private file paths and permissions.")
        return 2
    except (ValueError, RuntimeError) as error:
        # These exceptions are raised locally with fixed messages, never provider payloads.
        logger.error("%s", error)
        return 2
    finally:
        db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
