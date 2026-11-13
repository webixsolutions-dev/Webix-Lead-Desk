"""Native overview cards and a seven-day chart, populated only by real snapshots."""

import tkinter as tk
from collections.abc import Callable
from functools import partial
from tkinter import ttk
from zoneinfo import ZoneInfo

from app.desktop.metrics import PERIODS, DesktopSnapshot
from app.desktop.style import BG, BLUE, BORDER, INK, MUTED, RED, label, panel


class Overview:
    """Render aggregate data; no database or provider access in widgets."""

    def __init__(
        self,
        parent: tk.Misc,
        refresh: Callable[[], None],
        period_changed: Callable[[], None],
        open_page: Callable[[str], None],
    ) -> None:
        self.frame = tk.Frame(parent, bg=BG)
        self.snapshot: DesktopSnapshot | None = None
        heading = tk.Frame(self.frame, bg=BG)
        heading.pack(fill="x", pady=(0, 16))
        label(heading, "Workspace overview", size=23, bold=True, bg=BG).pack(anchor="w")
        label(heading, "See how your email outreach is progressing.", color=MUTED, bg=BG).pack(
            anchor="w", pady=(4, 0)
        )
        filters = tk.Frame(self.frame, bg=BG)
        filters.pack(fill="x", pady=(0, 12))
        self.period = tk.StringVar(value="Last 7 days")
        label(filters, "Activity period", bold=True, bg=BG).pack(side="left", padx=(0, 10))
        self.period_select = ttk.Combobox(
            filters, textvariable=self.period, values=PERIODS, state="readonly", width=14
        )
        self.period_select.pack(side="left")
        self.period_select.bind("<<ComboboxSelected>>", lambda _: period_changed())
        self.refresh_button = ttk.Button(filters, text="Refresh", command=refresh)
        self.refresh_button.pack(side="right")
        self.updated = tk.StringVar(value="Connect your workspace to load your numbers.")
        label(
            self.frame,
            "",
            color=MUTED,
            bg=BG,
            textvariable=self.updated,
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))
        cards = tk.Frame(self.frame, bg=BG)
        cards.pack(fill="x")
        self.values: dict[str, tk.StringVar] = {}
        for i, (key, title, detail, color) in enumerate(
            (
                ("contacted", "Businesses contacted", "Unique businesses emailed", INK),
                ("sent", "Emails sent", "Includes follow-ups and replies", RED),
                ("replied", "Businesses replied", "Human responses, including declines", BLUE),
            )
        ):
            cards.columnconfigure(i, weight=1, uniform="metrics")
            card = panel(cards, padx=14)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            label(card, title, bold=True).pack(anchor="w")
            value = tk.StringVar(value="—")
            self.values[key] = value
            label(card, "", size=30, bold=True, color=color, textvariable=value).pack(
                anchor="w", pady=(6, 4)
            )
            label(card, detail, size=9, color=MUTED, wraplength=210, justify="left").pack(
                anchor="w"
            )
        actions = tk.Frame(self.frame, bg=BG)
        actions.pack(fill="x", pady=(16, 0))
        label(actions, "Needs your attention", size=13, bold=True, bg=BG).pack(anchor="w")
        label(
            actions,
            "Current workspace totals · independent of the activity period",
            size=9,
            color=MUTED,
            bg=BG,
        ).pack(anchor="w", pady=(3, 9))
        row = tk.Frame(actions, bg=BG)
        row.pack(fill="x")
        for i, (key, title, path) in enumerate(
            (
                ("approvals", "Awaiting approval", "/conversations/drafts"),
                ("scheduled", "Scheduled emails", "/outbox"),
                ("attention", "Held / uncertain", "/outbox"),
                ("meetings", "Meetings · next 7 days", "/pipeline/bookings"),
            )
        ):
            row.columnconfigure(i, weight=1, uniform="actions")
            card = panel(row, padx=10, pady=10)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 4, 0 if i == 3 else 4))
            value = tk.StringVar(value="—")
            self.values[key] = value
            label(card, "", size=20, bold=True, textvariable=value).pack(anchor="w")
            button = tk.Button(
                card,
                text=title + "  ›",
                bg="white",
                fg=MUTED,
                relief="flat",
                cursor="hand2",
                anchor="w",
                wraplength=160,
                justify="left",
                command=partial(open_page, path),
            )
            button.pack(fill="x", pady=(4, 0))
        chart_panel = panel(self.frame)
        chart_panel.pack(fill="x", pady=(16, 0))
        chart_heading = tk.Frame(chart_panel, bg="white")
        chart_heading.pack(fill="x")
        label(chart_heading, "Daily email activity", size=13, bold=True).pack(side="left")
        label(chart_heading, "Last 7 calendar days", size=9, color=MUTED).pack(side="right")
        legend = tk.Frame(chart_panel, bg="white")
        legend.pack(fill="x", pady=(8, 0))
        label(legend, "■  Emails sent", size=9, color=RED).pack(side="left", padx=(0, 18))
        label(legend, "■  Businesses replied", size=9, color=BLUE).pack(side="left")
        self.chart = tk.Canvas(chart_panel, bg="white", height=170, highlightthickness=0)
        self.chart.pack(fill="x", pady=(4, 0))
        self.chart.bind("<Configure>", lambda _: self.draw_chart())
        label(
            self.frame,
            "Sent = accepted by your mailbox provider, not confirmed delivery. Test emails, "
            "automatic replies and delivery notices are excluded. Replies count when received; "
            "they may relate to emails sent before the selected period.",
            size=9,
            color=MUTED,
            bg=BG,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

    def clear(self) -> None:
        """Never leave another connection or period's numbers labelled as the new selection."""
        self.snapshot = None
        for value in self.values.values():
            value.set("—")
        self.updated.set("Waiting for workspace data…")
        self.draw_chart()

    def show(self, snapshot: DesktopSnapshot) -> None:
        """Apply a complete snapshot together on Tk's main thread."""
        self.snapshot = snapshot
        for key, value in self.values.items():
            value.set(f"{getattr(snapshot, key):,}")
        local = snapshot.checked_at.astimezone(ZoneInfo(snapshot.timezone))
        self.updated.set(
            f"Updated {local:%d %b, %I:%M %p} · {snapshot.timezone} · Refreshes every minute"
        )
        self.draw_chart()

    def failed(self) -> None:
        """Keep prior values visibly stale; a failure must never look like zero activity."""
        if self.snapshot:
            local = self.snapshot.checked_at.astimezone(ZoneInfo(self.snapshot.timezone))
            self.updated.set(
                f"Refresh paused · Showing previous data from {local:%d %b, %I:%M %p} "
                f"{self.snapshot.timezone}. Check the connection, then click Refresh."
            )
        else:
            self.updated.set(
                "Could not load activity. Check the connection, then click Refresh. "
                "Automatic refresh is paused."
            )

    def draw_chart(self) -> None:
        """Draw labelled paired bars; empty and unavailable data have distinct states."""
        self.chart.delete("all")
        width = max(self.chart.winfo_width(), 300)
        if not self.snapshot:
            self.chart.create_text(
                width / 2,
                80,
                text="Your activity chart will appear after connecting.",
                fill=MUTED,
                font=("Segoe UI", 10),
            )
            return
        days = self.snapshot.days
        maximum = max((max(day.sent, day.replied) for day in days), default=0)
        baseline = 138
        self.chart.create_line(12, baseline, width - 12, baseline, fill=BORDER)
        step = (width - 24) / 7
        for i, day in enumerate(days):
            x = 12 + step * (i + 0.5)
            for value, offset, color in ((day.sent, -12, RED), (day.replied, 12, BLUE)):
                height = (value / max(maximum, 1)) * 100
                if value:
                    self.chart.create_rectangle(
                        x + offset - 7,
                        baseline - height,
                        x + offset + 7,
                        baseline,
                        fill=color,
                        outline="",
                    )
                self.chart.create_text(
                    x + offset,
                    baseline - height - 10,
                    text=str(value),
                    fill=color,
                    font=("Segoe UI", 9, "bold"),
                )
            self.chart.create_text(x, 157, text=day.label, fill=MUTED, font=("Segoe UI", 9))
        if maximum == 0:
            self.chart.create_text(
                width / 2,
                52,
                text="No email activity recorded in the last 7 days.",
                fill=MUTED,
                font=("Segoe UI", 10),
            )
