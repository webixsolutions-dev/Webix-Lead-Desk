"""Capture synthetic Windows overview/setup screens without credentials or network calls."""

import sys
import tempfile
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path

from PIL import ImageGrab

from app.desktop.metrics import DailyActivity, DesktopSnapshot
from app.desktop.window import DesktopWindow


def capture(root: tk.Tk, destination: Path) -> None:
    """Capture only the synthetic app window into a CI artifact."""
    root.update()
    root.lift()
    root.after(800, root.quit)
    root.mainloop()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(destination)


def main() -> None:
    """Show labelled demo figures, not production activity or saved credentials."""
    destination = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as directory:
        root = tk.Tk()
        window = DesktopWindow(root, Path(directory), auto_refresh=False)
        window.show_page("overview")
        window.overview.show(
            DesktopSnapshot(
                datetime(2026, 11, 13, 8, tzinfo=UTC),
                "Asia/Karachi",
                "Last 7 days",
                42,
                58,
                11,
                6,
                8,
                2,
                3,
                tuple(
                    DailyActivity(day, sent, replied)
                    for day, sent, replied in zip(
                        ("Sat 07", "Sun 08", "Mon 09", "Tue 10", "Wed 11", "Thu 12", "Fri 13"),
                        (0, 0, 10, 12, 14, 13, 9),
                        (0, 0, 1, 3, 2, 3, 2),
                        strict=True,
                    )
                ),
            )
        )
        window.overview.updated.set(
            "DESIGN PREVIEW · Synthetic demo figures · No connected workspace"
        )
        capture(root, destination)
        root.geometry("980x680+0+0")
        capture(root, destination.with_stem(destination.stem + "-compact"))
        window.canvas.yview_moveto(1)
        capture(root, destination.with_stem(destination.stem + "-activity"))
        window.show_page("settings")
        capture(root, destination.with_stem(destination.stem + "-settings"))
        root.destroy()


if __name__ == "__main__":
    main()
