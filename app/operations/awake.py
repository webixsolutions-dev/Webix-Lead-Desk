"""Keep a supervised Windows PC awake only during Calgary work hours."""

import sys
from collections.abc import Callable
from datetime import datetime

from app.linkedin.timing import daytime


def keep_awake(now: datetime, *, enabled: bool, setter: Callable[[int], int] | None = None) -> None:
    """Prevent system sleep without changing power plans, display settings or other machines."""
    if setter is None:
        if sys.platform != "win32":
            return
        import ctypes

        setter = ctypes.windll.kernel32.SetThreadExecutionState
    assert setter is not None
    flags = 0x80000000 | (0x00000001 if enabled and daytime(now, planning=True) else 0)
    if setter(flags) == 0:
        import logging

        logging.getLogger(__name__).warning("Windows sleep-prevention request was not accepted")
