"""Thread-based desktop worker supervision without killing an in-flight job."""

from queue import Queue
from threading import Event, Thread

from sqlalchemy import text

from app.core.config import Settings
from app.db.migrate import migration_head
from app.db.session import create_database
from app.jobs.runner import run_worker


def check_connection(settings: Settings) -> None:
    """Read the schema version without migrating or changing production data."""
    db = create_database(settings)
    try:
        with db.engine.connect() as connection:
            if (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                != migration_head()
            ):
                raise RuntimeError("Database schema needs an administrator update")
    finally:
        db.engine.dispose()


class WorkerController:
    """Own one joinable worker thread and deliver fixed, secret-free status messages."""

    def __init__(self) -> None:
        self.messages: Queue[str] = Queue()
        self.stop_event = Event()
        self.thread: Thread | None = None

    @property
    def active(self) -> bool:
        """Keep controls locked until the thread and its current job really exit."""
        return self.thread is not None and self.thread.is_alive()

    def start(self, settings: Settings, *, check_only: bool = False) -> None:
        """Perform network operations off the UI thread; reject duplicate starts."""
        if self.active:
            raise RuntimeError("Worker or connection check already active")
        self.stop_event.clear()
        self.thread = Thread(target=self._run, args=(settings, check_only), daemon=False)
        self.thread.start()

    def stop(self) -> None:
        """Request a cooperative stop after the current operation completes."""
        self.stop_event.set()
        self.messages.put("Stopping — waiting for the current operation to finish…")

    def _run(self, settings: Settings, check_only: bool) -> None:
        try:
            check_connection(settings)
            if self.stop_event.is_set():
                return
            if check_only:
                self.messages.put("Connection verified. Ready to start.")
                return
            db = create_database(settings)
            try:

                def started() -> None:
                    self.messages.put("Running — connected to your dashboard.")

                run_worker(db, settings, stop_event=self.stop_event, on_started=started)
            finally:
                db.engine.dispose()
            self.messages.put("Worker stopped safely.")
        except Exception:
            # Do not interpolate database errors, URLs, passwords or provider payloads.
            self.messages.put(
                "Could not run. Check connection details, database availability and schema; "
                "also stop any other worker before retrying."
            )
