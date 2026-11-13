"""Windows overview, private setup and worker controls with background database reads."""

import tkinter as tk
import webbrowser
from functools import partial
from pathlib import Path
from time import monotonic
from tkinter import filedialog, messagebox, ttk
from typing import cast

from app.desktop.analytics import AnalyticsController
from app.desktop.config import DEFAULT_DASHBOARD_URL, DesktopConfig, load_config, save_config
from app.desktop.controller import WorkerController
from app.desktop.metrics import Period
from app.desktop.overview import Overview
from app.desktop.platform import set_startup
from app.desktop.setup_view import build_settings
from app.desktop.style import BG, BORDER, MUTED, NAVY, RED, configure, label


class DesktopWindow:
    """Keep widgets on Tk's main thread and database reads on background threads."""

    def __init__(
        self, root: tk.Tk, directory: Path, *, autostart: bool = False, auto_refresh: bool = True
    ) -> None:
        self.root, self.directory = root, directory
        self.controller = WorkerController()
        self.analytics = AnalyticsController()
        self.auto_refresh = auto_refresh
        self.generation = 0
        self.refresh_due = monotonic() + 1
        self.config: DesktopConfig | None = None
        self.closing = self.locked = False
        self.widgets: list[ttk.Entry | ttk.Button | ttk.Checkbutton] = []
        self.database, self.encryption = tk.StringVar(), tk.StringVar()
        self.dashboard = tk.StringVar(value=DEFAULT_DASHBOARD_URL)
        self.backup = tk.StringVar(value=str(directory / "backups"))
        self.startup = tk.BooleanVar()
        self.status = tk.StringVar(value="Save your connection details to get started.")
        root.title("Webix Lead Desk")
        width = max(980, min(1180, root.winfo_screenwidth() - 60))
        minimum_height = max(500, min(680, root.winfo_screenheight() - 100))
        height = max(minimum_height, min(820, root.winfo_screenheight() - 100))
        left = max(0, (root.winfo_screenwidth() - width) // 2)
        top = max(0, (root.winfo_screenheight() - height - 80) // 2)
        root.geometry(f"{width}x{height}+{left}+{top}")
        root.minsize(980, minimum_height)
        configure(root)
        sidebar = tk.Frame(root, bg=NAVY, width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        label(sidebar, "W", size=25, color="white", bold=True, bg=RED, padx=12, pady=4).pack(
            anchor="w", padx=22, pady=(26, 12)
        )
        label(sidebar, "Webix Lead Desk", size=15, color="white", bold=True, bg=NAVY).pack(
            anchor="w", padx=20
        )
        label(sidebar, "DESKTOP WORKSPACE", size=9, color="#B6C4D7", bg=NAVY).pack(
            anchor="w", padx=20, pady=(4, 28)
        )
        self.nav: dict[str, tk.Button] = {}
        for key, title in (("overview", "Overview"), ("settings", "Connection settings")):
            button = tk.Button(
                sidebar,
                text=title,
                anchor="w",
                relief="flat",
                bd=0,
                padx=14,
                pady=12,
                fg="white",
                bg=NAVY,
                cursor="hand2",
                activebackground="#31445F",
                activeforeground="white",
                command=partial(self.show_page, key),
            )
            button.pack(fill="x", padx=10, pady=3)
            self.nav[key] = button
        label(sidebar, "v0.33.0", size=9, color="#B6C4D7", bg=NAVY).pack(
            side="bottom", anchor="w", padx=22, pady=18
        )
        self.worker_badge = tk.StringVar(value="Worker stopped")
        label(sidebar, "", textvariable=self.worker_badge, bold=True, color="white", bg=NAVY).pack(
            side="bottom", anchor="w", padx=22, pady=(10, 0)
        )
        label(
            sidebar,
            "Keep this app open\nto process workspace jobs.",
            color="#B6C4D7",
            size=9,
            bg=NAVY,
            justify="left",
        ).pack(side="bottom", anchor="w", padx=22)
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg="white", padx=24, pady=14)
        header.pack(fill="x")
        label(header, "Your workspace, at a glance", bold=True).pack(side="left")
        ttk.Button(header, text="Open dashboard ↗", command=self.open_dashboard).pack(side="right")
        tk.Frame(main, bg=BORDER, height=1).pack(fill="x")
        footer = tk.Frame(main, bg="white", padx=18, pady=12)
        footer.pack(side="bottom", fill="x")
        controls = tk.Frame(footer, bg="white")
        controls.pack(side="right", padx=(12, 0))
        self.start_button = ttk.Button(
            controls, text="Start worker", style="Accent.TButton", command=self.start
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.widgets.append(self.start_button)
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        label(footer, "", textvariable=self.status, wraplength=470, justify="left").pack(anchor="w")
        label(footer, "Outreach OFF · Approved connection tests only", size=9, color=MUTED).pack(
            anchor="w", pady=(3, 0)
        )
        viewport = tk.Frame(main, bg=BG)
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(self.canvas, bg=BG, padx=24, pady=22)
        item = self.canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda event: self.canvas.itemconfigure(item, width=event.width)
        )
        root.bind(
            "<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units")
        )
        self.overview = Overview(content, self.refresh, self.change_period, self.open_page)
        self.pages = {"overview": self.overview.frame, "settings": build_settings(content, self)}
        try:
            self.config = load_config(directory)
            if self.config:
                self.database.set(self.config.database_url.get_secret_value())
                self.encryption.set(self.config.encryption_key.get_secret_value())
                self.dashboard.set(self.config.dashboard_url)
                self.backup.set(self.config.backup_directory)
                self.startup.set(self.config.autostart)
                self.status.set("Settings loaded. Ready to start.")
        except Exception:
            self.status.set(
                "Saved settings could not be unlocked. Use the original Windows account."
            )
            # Do not overwrite an unreadable key file with a new set of backup keys.
            for widget in self.widgets:
                widget.configure(state="disabled")
            self.root.after(
                100,
                lambda: messagebox.showerror(
                    "Settings locked",
                    "Use the original Windows account. Your saved settings were preserved.",
                ),
            )
            self.locked = True
        else:
            self.locked = False
        self.show_page("overview" if self.config else "settings")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)
        if autostart and self.config and self.config.autostart and not self.locked:
            root.after(500, self.start)
            root.after(600, root.iconify)

    def show_page(self, page: str) -> None:
        """Navigate locally without saving settings or starting a worker."""
        for key, frame in self.pages.items():
            frame.pack_forget()
            self.nav[key].configure(bg="#31445F" if key == page else NAVY)
        self.pages[page].pack(fill="both", expand=True)
        self.canvas.yview_moveto(0)

    def change_period(self) -> None:
        """Invalidate in-flight snapshots when the activity period changes."""
        self.generation += 1
        self.overview.clear()
        self.refresh_due = 0
        self.refresh()

    def refresh(self) -> None:
        """Read saved workspace data only; no save, worker start or provider action."""
        if self.locked or self.closing or not self.config:
            self.overview.updated.set(
                "Save your workspace connection in Connection settings first."
            )
            return
        if self.analytics.refresh(
            self.config.settings(self.directory),
            cast(Period, self.overview.period.get()),
            self.generation,
        ):
            self.overview.updated.set("Refreshing workspace activity…")
            self.refresh_due = float("inf")

    def choose_folder(self) -> None:
        """Choose a local backup folder without exposing configuration contents."""
        selected = filedialog.askdirectory(title="Encrypted backup folder")
        if selected:
            self.backup.set(selected)

    def save(self) -> bool:
        """Validate and persist edited settings, preserving generated keys on updates."""
        if self.controller.active or self.locked:
            return False
        try:
            values = self.config.model_dump() if self.config else {}
            values.update(
                database_url=self.database.get().strip(),
                encryption_key=self.encryption.get().strip(),
                dashboard_url=self.dashboard.get().strip(),
                backup_directory=self.backup.get().strip(),
                autostart=self.startup.get(),
            )
            config = DesktopConfig.model_validate(values)
            config.settings(self.directory)
            save_config(self.directory, config)
            changed = self.config != config
            self.config = config
            if changed:
                self.generation += 1
                self.overview.clear()
                self.refresh_due = 0
            set_startup(config.autostart)
        except Exception:
            self.status.set(
                "Could not save. Check the pooler URL, encryption key and backup folder."
            )
            return False
        self.status.set("Settings saved privately on this PC.")
        return True

    def check(self) -> None:
        """Check the configured database without blocking the window."""
        if self.save() and self.config:
            self.status.set("Checking database connection…")
            self.controller.start(self.config.settings(self.directory), check_only=True)

    def start(self) -> None:
        """Start only a configured worker; environment variables cannot enable sending."""
        if self.save() and self.config:
            self.status.set("Connecting and starting worker…")
            self.controller.start(self.config.settings(self.directory))

    def stop(self) -> None:
        """Ask the active operation to finish before stopping the worker."""
        if self.controller.active:
            self.controller.stop()

    def recovery_key(self) -> None:
        """Let the owner privately copy the recovery key into a password manager."""
        if self.config is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Save your backup recovery key privately")
        dialog.geometry("580x150")
        ttk.Label(dialog, text="Keep this in your password manager, separate from backups.").pack(
            pady=12
        )
        value = self.config.backup_key.get_secret_value()
        entry = ttk.Entry(dialog, show="•", width=60)
        entry.insert(0, value)
        entry.configure(state="readonly")
        entry.pack(padx=16)

        def copy() -> None:
            dialog.clipboard_clear()
            dialog.clipboard_append(value)
            dialog.update()

        ttk.Button(dialog, text="Copy recovery key", command=copy).pack(pady=12)

    def open_page(self, path: str) -> None:
        """Open fixed destinations using the saved validated HTTPS origin."""
        if path not in {"", "/conversations/drafts", "/outbox", "/pipeline/bookings"}:
            return
        origin = (
            self.config.settings(self.directory).public_base_url
            if self.config
            else DEFAULT_DASHBOARD_URL
        )
        webbrowser.open(origin.rstrip("/") + path)

    def open_dashboard(self) -> None:
        """Open the complete hosted workspace in the default browser."""
        self.open_page("")

    def close(self) -> None:
        """Never destroy the process while a worker job is still in flight."""
        if self.controller.active:
            if not self.closing and messagebox.askyesno(
                "Stop worker?",
                "Finish the current operation and then close? "
                "Keep this app open to continue processing jobs.",
            ):
                self.closing = True
                self.controller.stop()
        else:
            self.root.destroy()

    def poll(self) -> None:
        """Apply queue results on Tk's thread; pause refreshes after a connection failure."""
        while not self.controller.messages.empty():
            self.status.set(self.controller.messages.get_nowait())
        active = self.controller.active
        self.worker_badge.set(self.controller.state_label)
        if not self.locked:
            for widget in self.widgets:
                widget.configure(state="disabled" if active or self.closing else "normal")
        self.stop_button.configure(state="normal" if active and not self.closing else "disabled")
        if self.closing and not active:
            self.root.destroy()
            return
        while not self.analytics.results.empty():
            result = self.analytics.results.get_nowait()
            if result.generation != self.generation:
                continue
            if result.snapshot:
                self.overview.show(result.snapshot)
                self.refresh_due = monotonic() + 60
            else:
                self.overview.failed()
                self.refresh_due = float("inf")
        if (
            self.auto_refresh
            and self.config
            and not self.locked
            and not self.closing
            and monotonic() >= self.refresh_due
        ):
            self.refresh()
        self.overview.refresh_button.configure(
            state="disabled" if self.analytics.active or self.locked or self.closing else "normal"
        )
        self.root.after(200, self.poll)
