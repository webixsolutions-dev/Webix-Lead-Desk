"""Serialized, read-only desktop refreshes with no automatic retry on failure."""

from dataclasses import dataclass
from queue import Queue
from threading import Thread

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.session import create_database
from app.desktop.metrics import DesktopSnapshot, Period, read_snapshot


@dataclass(frozen=True)
class SnapshotResult:
    """Associate a safe result with the saved connection and requested period."""

    generation: int
    snapshot: DesktopSnapshot | None


class AnalyticsController:
    """Keep database reads off Tk's thread and return sanitized results only."""

    def __init__(self) -> None:
        self.results: Queue[SnapshotResult] = Queue()
        self.thread: Thread | None = None

    @property
    def active(self) -> bool:
        """Prevent overlapping queries, including repeated Refresh clicks."""
        return self.thread is not None and self.thread.is_alive()

    def refresh(self, settings: Settings, period: Period, generation: int) -> bool:
        """Start at most one read; the UI decides when another refresh is warranted."""
        if self.active:
            return False
        self.thread = Thread(target=self._read, args=(settings, period, generation), daemon=True)
        self.thread.start()
        return True

    def _read(self, settings: Settings, period: Period, generation: int) -> None:
        snapshot = None
        try:
            db = create_database(settings)
            try:
                snapshot = read_snapshot(db, settings, period, utc_now())
            finally:
                db.engine.dispose()
        except Exception:
            # No URLs, credentials, raw SQL or provider exception text in UI/logs.
            pass
        self.results.put(SnapshotResult(generation, snapshot))
