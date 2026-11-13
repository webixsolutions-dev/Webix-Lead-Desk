"""Private connection form, separated from the everyday analytics overview."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from app.desktop.style import BG, MUTED, label, panel

if TYPE_CHECKING:
    from app.desktop.window import DesktopWindow


def build_settings(parent: tk.Misc, window: "DesktopWindow") -> tk.Frame:
    """Preserve encrypted settings and existing worker controls in a simpler form."""
    frame = tk.Frame(parent, bg=BG)
    label(frame, "Connection settings", size=23, bold=True, bg=BG).pack(anchor="w")
    label(
        frame, "Set up once. Your saved details stay on this Windows account.", color=MUTED, bg=BG
    ).pack(anchor="w", pady=(4, 18))
    card = panel(frame, padx=22, pady=20)
    card.pack(fill="x")
    label(card, "Workspace connection", size=14, bold=True).pack(anchor="w")
    label(card, "Use the same database and encryption key as your dashboard.", color=MUTED).pack(
        anchor="w", pady=(4, 14)
    )
    for text, variable, hidden, browse in (
        ("Database connection URL", window.database, True, False),
        ("Encryption key", window.encryption, True, False),
        ("Dashboard address", window.dashboard, False, False),
        ("Encrypted backup folder · weekly, latest 8 kept", window.backup, False, True),
    ):
        label(card, text, bold=True).pack(anchor="w", pady=(8, 4))
        line = ttk.Frame(card)
        line.pack(fill="x")
        entry = ttk.Entry(line, textvariable=variable, show="•" if hidden else "")
        entry.pack(side="left", fill="x", expand=True)
        window.widgets.append(entry)
        if browse:
            button = ttk.Button(line, text="Browse…", command=window.choose_folder)
            button.pack(side="right", padx=(8, 0))
            window.widgets.append(button)
    startup = ttk.Checkbutton(
        card, text="Start worker when I sign in to Windows", variable=window.startup
    )
    startup.pack(anchor="w", pady=(18, 12))
    window.widgets.append(startup)
    for text, enabled in (
        ("Prepare AI assessments and email drafts using the workspace allowance", window.ai_drafts),
        ("Deliver approved and scheduled emails from enabled mailboxes", window.live_email),
    ):
        checkbox = ttk.Checkbutton(card, text=text, variable=enabled)
        checkbox.pack(anchor="w", pady=(0, 8))
        window.widgets.append(checkbox)
    actions = ttk.Frame(card)
    actions.pack(fill="x")
    for text, action, style in (
        ("Save settings", window.save, "Accent.TButton"),
        ("Test connection", window.check, "TButton"),
    ):
        button = ttk.Button(actions, text=text, command=action, style=style)
        button.pack(side="left", padx=(0, 8))
        window.widgets.append(button)
    ttk.Button(actions, text="Backup recovery key", command=window.recovery_key).pack(side="right")
    label(
        frame,
        "Manage your OpenAI key, search budget and mailbox readiness in the dashboard.",
        color=MUTED,
        bg=BG,
        wraplength=680,
        justify="left",
    ).pack(anchor="w", pady=16)
    return frame
