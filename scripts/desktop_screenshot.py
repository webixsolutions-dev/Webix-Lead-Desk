"""Capture a synthetic Windows setup screen; no credentials or production network calls."""

import sys
import tempfile
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

from app.desktop.window import DesktopWindow


def main() -> None:
    """Capture only the app window into the CI artifact, never commit captures."""
    destination = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as directory:
        root = tk.Tk()
        DesktopWindow(root, Path(directory))
        root.update()
        root.lift()
        root.after(800, root.quit)
        root.mainloop()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(
            destination
        )
        root.destroy()


if __name__ == "__main__":
    main()
