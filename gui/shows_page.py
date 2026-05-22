"""
gui/shows_page.py
Streamsaver — Currently watched shows tab
Author: Noah Lee

Displays all tracked TV shows with watch status, episode info, and
abandoned state. Users can manually mark/unmark shows as abandoned.
"""

import tkinter as tk
from tkinter import ttk, messagebox

from gui import theme
from gui.widgets import SectionHeader
from core.abandoned import set_manual_abandoned
from db import db_session

COL_DEFS = [
    ("Title",         220, "w"),
    ("Service",       130, "w"),
    ("Status",        110, "center"),
    ("Progress",      110, "center"),
    ("Next Episode",  100, "center"),
    ("Next Premiere", 110, "center"),
    ("Last Watched",  100, "center"),
    ("Abandoned",      80, "center"),
]


class ShowsPage(ttk.Frame):
    """Tab: view and manage watched TV shows."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(style="TFrame")
        self._build_ui()
        self.refresh()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
        SectionHeader(header, text="Watched Shows").pack(side="left")

        btn_right = ttk.Frame(header, style="TFrame")
        btn_right.pack(side="right")
        ttk.Button(btn_right, text="Mark Abandoned",   style="Danger.TButton",
                   command=lambda: self._toggle_abandoned(True)).pack(
                       side="right", padx=(theme.PAD_SMALL, 0))
        ttk.Button(btn_right, text="Unmark Abandoned",
                   command=lambda: self._toggle_abandoned(False)).pack(
                       side="right", padx=(theme.PAD_SMALL, 0))

        # Filter bar
        filter_bar = ttk.Frame(self, style="TFrame")
        filter_bar.pack(fill="x", padx=theme.PAD, pady=theme.PAD_SMALL)
        ttk.Label(filter_bar, text="Filter:", style="Dim.TLabel").pack(side="left")
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(filter_bar, textvariable=self._filter_var, width=30).pack(
            side="left", padx=(theme.PAD_SMALL, theme.PAD)
        )
        ttk.Label(filter_bar, text="Show:", style="Dim.TLabel").pack(side="left")
        self._show_filter = tk.StringVar(value="All")
        cb = ttk.Combobox(
            filter_bar, textvariable=self._show_filter,
            values=["All", "Active", "Abandoned", "Ended"],
            state="readonly", width=12,
        )
        cb.pack(side="left", padx=theme.PAD_SMALL)
        cb.bind("<<ComboboxSelected>>", lambda *_: self.refresh())

        # Treeview
        tree_frame = ttk.Frame(self, style="TFrame")
        tree_frame.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD_SMALL)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in COL_DEFS],
            show="headings",
            selectmode="browse",
        )
        for col, width, anchor in COL_DEFS:
            self._tree.heading(col, text=col,
                               command=lambda c=col: self._sort_by(c))
            self._tree.column(col, width=width, anchor=anchor,
                              stretch=(col == "Title"))

        self._tree.tag_configure("abandoned", foreground=theme.COMMENT)
        self._tree.tag_configure("ended",     foreground=theme.FG_DIM)
        self._tree.tag_configure("returning", foreground=theme.GREEN)
        self._tree.tag_configure("canceled",  foreground=theme.DANGER)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",  command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Summary bar
        self._summary = ttk.Label(
            self, style="Dim.TLabel", font=theme.FONT_SMALL
        )
        self._summary.pack(anchor="w", padx=theme.PAD, pady=(0, theme.PAD))

        self._sort_col = "Title"
        self._sort_asc = True

    # ── Data ───────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

        text_filter   = self._filter_var.get().lower()
        status_filter = self._show_filter.get()

        with db_session() as conn:
            rows = conn.execute(
                """
                SELECT s.trakt_id, s.title, s.status, s.current_season,
                       s.total_seasons, s.last_watched_episode, s.next_episode,
                       s.next_premiere_date, s.last_watch_date,
                       s.is_abandoned, svc.name AS service_name
                FROM   shows s
                LEFT JOIN services svc ON s.service_id = svc.id
                ORDER BY LOWER(s.title)
                """
            ).fetchall()

        total = 0
        for row in rows:
            row = dict(row)

            # Text filter
            if text_filter and text_filter not in row["title"].lower():
                continue

            # Status filter
            if status_filter == "Active"    and (row["is_abandoned"] or row["status"] in ("ended","canceled")):
                continue
            if status_filter == "Abandoned" and not row["is_abandoned"]:
                continue
            if status_filter == "Ended"     and row["status"] not in ("ended","canceled"):
                continue

            progress = (
                f"S{row['current_season']:02d} / S{row['total_seasons']:02d}"
                if row["current_season"] else "—"
            )
            tag = "abandoned" if row["is_abandoned"] else (row["status"] or "")
            abandoned_mark = "✓" if row["is_abandoned"] else ""

            self._tree.insert(
                "", "end",
                iid=row["trakt_id"],
                values=(
                    row["title"],
                    row["service_name"] or "—",
                    (row["status"] or "").title(),
                    progress,
                    row["next_episode"]        or "—",
                    row["next_premiere_date"]  or "—",
                    row["last_watch_date"]     or "—",
                    abandoned_mark,
                ),
                tags=(tag,),
            )
            total += 1

        self._summary.config(text=f"{total} show(s) displayed")

    def _selected_trakt_id(self) -> str | None:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        items = [(self._tree.set(k, col), k) for k in self._tree.get_children("")]
        items.sort(reverse=not self._sort_asc)
        for idx, (_, k) in enumerate(items):
            self._tree.move(k, "", idx)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _toggle_abandoned(self, abandoned: bool) -> None:
        tid = self._selected_trakt_id()
        if not tid:
            messagebox.showinfo("No selection", "Please select a show.", parent=self)
            return
        action = "mark" if abandoned else "unmark"
        title  = self._tree.item(tid, "values")[0]
        if messagebox.askyesno(
            "Confirm", f"{action.title()} '{title}' as abandoned?", parent=self
        ):
            set_manual_abandoned(tid, abandoned)
            self.refresh()
