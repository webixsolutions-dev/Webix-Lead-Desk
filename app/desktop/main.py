"""Console-free executable entry point, including an offline packaged smoke test."""

import argparse
import json
import logging
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from app.desktop.platform import acquire_instance, data_directory, release_instance
from app.desktop.window import DesktopWindow


def smoke_test(result: Path) -> int:
    """Exercise bundled UI, DPAPI, migrations, PostgreSQL driver and an offline job."""
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    from sqlalchemy import select

    from app.core.clock import utc_now
    from app.core.config import Settings
    from app.db.migrate import migrate
    from app.db.models import Job
    from app.db.session import create_database
    from app.desktop.config import DesktopConfig, load_config, save_config
    from app.jobs.queue import enqueue_health
    from app.jobs.runner import run_worker

    try:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            config = DesktopConfig(
                database_url=SecretStr(
                    "postgresql+psycopg://offline@localhost/test?sslmode=require"
                ),
                encryption_key=SecretStr(Fernet.generate_key().decode()),
                backup_directory=str(directory / "backups"),
            )
            save_config(directory, config)
            loaded = load_config(directory)
            assert loaded is not None and loaded.encryption_key == config.encryption_key
            postgres = create_database(config.settings(directory))
            postgres.engine.dispose()  # Load bundled psycopg/libpq without any network connection.
            settings = Settings(
                _env_file=None,
                database_url="sqlite:///" + str(directory / "smoke.sqlite"),
                session_secret=config.session_secret,
            )
            migrate(settings)
            db = create_database(settings)
            try:
                job = enqueue_health(db, "packaged-smoke", utc_now())
                run_worker(db, settings, once=True)
                with db.sessions() as session:
                    assert session.scalar(select(Job.state).where(Job.id == job.id)) == "succeeded"
            finally:
                db.engine.dispose()
            root = tk.Tk()
            DesktopWindow(root, directory, auto_refresh=False)
            root.update()
            root.destroy()
        result.write_text(
            json.dumps(
                {
                    "ok": True,
                    "checks": [
                        "dpapi",
                        "postgres-driver",
                        "migrations",
                        "worker-job",
                        "desktop-ui",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return 0
    except Exception:
        result.write_text('{"ok": false}', encoding="utf-8")
        return 1


def main() -> int:
    """Launch the companion without ever writing secret exception text to a log."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--smoke-test", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.smoke_test:
        return smoke_test(args.smoke_test)
    handle: int | None = None
    try:
        if sys.platform != "win32":
            raise RuntimeError("The installer requires Windows")
        handle = acquire_instance()
        root = tk.Tk()
        # Tk's default callback traceback may include private configuration values.
        root.report_callback_exception = lambda *_: messagebox.showerror(
            "Action failed", "The action could not complete. Check your settings and connection."
        )
        DesktopWindow(root, data_directory(), autostart=args.autostart)
        root.mainloop()
        return 0
    except Exception:
        messagebox.showerror(
            "Webix Lead Desk",
            "Could not open the app. Check whether it is already running. "
            "Use the original Windows account.",
        )
        return 1
    finally:
        if handle is not None:
            release_instance(handle)


if __name__ == "__main__":
    raise SystemExit(main())
