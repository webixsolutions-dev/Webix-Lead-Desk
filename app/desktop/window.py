"""Small Windows companion: private setup, worker controls and hosted dashboard link."""

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.desktop.config import DesktopConfig, load_config, save_config
from app.desktop.controller import WorkerController
from app.desktop.platform import set_startup

RED = "#ED1C24"
DARK = "#202024"


class DesktopWindow:
    """Keep all widget access on the UI thread and network work in the controller."""

    def __init__(self, root: tk.Tk, directory: Path, *, autostart: bool = False) -> None:
        self.root = root
        self.directory = directory
        self.controller = WorkerController()
        self.config: DesktopConfig | None = None
        self.closing = False
        self.widgets: list[ttk.Entry | ttk.Button | ttk.Checkbutton] = []
        root.title("Webix Lead Desk · PC Worker")
        root.geometry("860x730")
        root.minsize(760, 690)
        root.configure(bg="#F4F5F7")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#FFFFFF", foreground=DARK, font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 9))
        style.configure("Accent.TButton", background=RED, foreground="white")
        style.map("Accent.TButton", background=[("active", "#C9151D"), ("disabled", "#D7D7DC")])
        style.configure("TEntry", padding=7, font=("Segoe UI", 10))
        style.configure("TCheckbutton", background="white", font=("Segoe UI", 10))
        header = tk.Frame(root, bg="white", padx=28, pady=16)
        header.pack(fill="x")
        logo_path = Path(__file__).parents[1] / "ui" / "static" / "webix-logo.png"
        self.logo: tk.PhotoImage | None = None
        if logo_path.exists():
            original = tk.PhotoImage(file=str(logo_path))
            self.logo = original.subsample(max(1, original.width() // 180))
            tk.Label(header, image=self.logo, bg="white").pack(side="left", padx=(0, 22))
        tk.Label(header, text="LEAD DESK", font=("Segoe UI", 22, "bold"), bg="white", fg=DARK).pack(
            anchor="w"
        )
        tk.Label(
            header,
            text="Your Windows worker companion",
            font=("Segoe UI", 10),
            bg="white",
            fg="#666671",
        ).pack(anchor="w")
        tk.Frame(root, bg=RED, height=3).pack(fill="x")
        card = ttk.Frame(root, padding=24)
        card.pack(fill="both", expand=True, padx=24, pady=20)
        card.columnconfigure(0, weight=1)
        ttk.Label(
            card, text="Connect once. Work from your dashboard.", font=("Segoe UI", 15, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(
            card,
            text="Settings are encrypted for this Windows account. Keep your keys privately.",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))
        self.database = tk.StringVar()
        self.encryption = tk.StringVar()
        self.dashboard = tk.StringVar(value="https://webix-lead-desk.vercel.app")
        self.backup = tk.StringVar(value=str(directory / "backups"))
        self.startup = tk.BooleanVar()
        for row, label, variable, hidden in (
            (2, "Database connection URL · same as Vercel", self.database, True),
            (4, "Encryption key · same as Vercel", self.encryption, True),
            (6, "Dashboard address", self.dashboard, False),
            (8, "Encrypted backup folder · weekly, latest 8 kept", self.backup, False),
        ):
            ttk.Label(card, text=label).grid(row=row, column=0, sticky="w", pady=(7, 3))
            entry = ttk.Entry(card, textvariable=variable, show="•" if hidden else "")
            entry.grid(row=row + 1, column=0, sticky="ew")
            self.widgets.append(entry)
        folder = ttk.Button(card, text="Choose backup folder", command=self.choose_folder)
        folder.grid(row=10, column=0, sticky="w", pady=(8, 8))
        self.widgets.append(folder)
        startup = ttk.Checkbutton(
            card, text="Start worker when I sign in to Windows", variable=self.startup
        )
        startup.grid(row=11, column=0, sticky="w")
        self.widgets.append(startup)
        actions = ttk.Frame(card)
        actions.grid(row=12, column=0, sticky="ew", pady=(18, 14))
        for label, callback in (("Save settings", self.save), ("Test connection", self.check)):
            button = ttk.Button(actions, text=label, command=callback)
            button.pack(side="left", padx=(0, 8))
            self.widgets.append(button)
        self.start_button = ttk.Button(
            actions, text="Start Worker", style="Accent.TButton", command=self.start
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.widgets.append(self.start_button)
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop)
        self.stop_button.pack(side="left")
        self.status = tk.StringVar(value="Save your connection details to get started.")
        ttk.Label(
            card, textvariable=self.status, wraplength=710, font=("Segoe UI", 11, "bold")
        ).grid(row=13, column=0, sticky="w", pady=(0, 8))
        ttk.Label(card, text="Live sending is OFF. Keep this app open while jobs run.").grid(
            row=14, column=0, sticky="w"
        )
        links = ttk.Frame(card)
        links.grid(row=15, column=0, sticky="w", pady=(14, 0))
        ttk.Button(links, text="Open Dashboard ↗", command=self.open_dashboard).pack(side="left")
        ttk.Button(links, text="Backup recovery key", command=self.recovery_key).pack(
            side="left", padx=8
        )
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
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)
        if autostart and self.config and self.config.autostart and not self.locked:
            root.after(500, self.start)
            root.after(600, root.iconify)

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
            self.config = config
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

    def open_dashboard(self) -> None:
        """Open only the validated saved HTTPS origin in the default browser."""
        if self.config:
            webbrowser.open(self.config.settings(self.directory).public_base_url)
        else:
            webbrowser.open("https://webix-lead-desk.vercel.app")

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
        """Apply queued state messages only on Tk's main thread."""
        while not self.controller.messages.empty():
            self.status.set(self.controller.messages.get_nowait())
        active = self.controller.active
        if not self.locked:
            for widget in self.widgets:
                widget.configure(state="disabled" if active or self.closing else "normal")
        self.stop_button.configure(state="normal" if active and not self.closing else "disabled")
        if self.closing and not active:
            self.root.destroy()
            return
        self.root.after(200, self.poll)
