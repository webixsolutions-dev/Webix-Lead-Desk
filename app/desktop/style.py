"""Native desktop visual system, aligned with the hosted workspace."""

import tkinter as tk
from tkinter import ttk
from typing import Any

RED = "#BC0F24"
NAVY = "#172438"
INK = "#172438"
MUTED = "#58677B"
BG = "#F3F5F9"
BORDER = "#DCE2EB"
BLUE = "#2563A6"


def configure(root: tk.Tk) -> None:
    """Use consistent typography and keyboard focus on Windows and preview environments."""
    root.configure(bg=BG)
    root.option_add("*Font", ("Segoe UI", 10))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="white")
    style.configure("TLabel", background="white", foreground=INK, font=("Segoe UI", 10))
    style.configure(
        "TButton", font=("Segoe UI", 10), padding=(14, 9), background="white", foreground=INK
    )
    style.configure("Accent.TButton", background=RED, foreground="white")
    style.map(
        "Accent.TButton",
        background=[("active", "#A10C1F"), ("disabled", "#D9DEE7")],
        foreground=[("disabled", "#58677B")],
    )
    style.configure("TEntry", padding=7, font=("Segoe UI", 10))
    style.configure("TCombobox", padding=6, font=("Segoe UI", 10))
    style.configure("TCheckbutton", background="white", foreground=INK, font=("Segoe UI", 10))


def label(
    parent: tk.Misc,
    text: str,
    *,
    size: int = 10,
    color: str = INK,
    bold: bool = False,
    bg: str = "white",
    **kwargs: Any,
) -> tk.Label:
    """Create a readable native label using the shared palette."""
    return tk.Label(
        parent,
        text=text,
        font=("Segoe UI", size, "bold" if bold else "normal"),
        fg=color,
        bg=bg,
        **kwargs,
    )


def panel(parent: tk.Misc, *, padx: int = 18, pady: int = 16) -> tk.Frame:
    """A white card with a quiet border, without bitmap dependencies."""
    return tk.Frame(
        parent, bg="white", padx=padx, pady=pady, highlightbackground=BORDER, highlightthickness=1
    )
