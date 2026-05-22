"""
gui/shows_page.py
Streamsaver — Currently watched shows tab
Author: Noah Lee

Displays tracked TV shows with interest score, show state, and provider info.
Users can manually mark/unmark shows as abandoned (sets abandoned_override).
"""

import tkinter as tk
from tkinter import ttk, messagebox

from gui import theme
from gui.widgets import SectionHeader
from db import db_session

# show_state → (display label, color tag)
_STATE_DISPLAY = {
    "active":      ("Active",      "active"),
    "occasional":  ("Occasional",  "occasional"),
    "dormant":     ("Dormant",     "dormant"),
    "abandoned":   ("Abandoned",   "abandoned"),
    "anticipated": ("Anticipated", "anticipated"),
    "ignored":     ("Ignored",     "ignored"),
    "archived":    ("Archived",    "archived"),
}

COL_DEFS = [
    ("Title",          220, "w"),
    ("Service",        130, "w"),
    ("State",           90, "center"),
    ("Score",           55, "center"),
    ("Provider",       130, "w"),
    ("Last Watched",   100, "center"),
    ("Next Air Date",  100, "center"),
    ("Trakt Status",   110, "center"),
]

_FILTER_STATES = ["All", "Active", "Occasional", "Dormant",
                  "Abandoned", "Anticipated", "Archived"]


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
        ttk.Label(filter_bar, text="State:", style="Dim.TLabel").pack(side="left")
        self._show_filter = tk.StringVar(value="All")
        cb = ttk.Combobox(
            filter_bar, textvariable=self._show_filter,
            values=_FILTER_STATES,
            state="readonly", width=14,
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

        # Row color tags per show_state
        self._tree.tag_configure("active",      foreground=theme.GREEN)
        self._tree.tag_configure("occasional",  foreground=theme.CYAN)
        self._tree.tag_configure("anticipated", foreground=theme.YELLOW)
        self._tree.tag_configure("dormant",     foreground=theme.FG_DIM)
        self._tree.tag_configure("abandoned",   foreground=theme.COMMENT)
        self._tree.tag_configure("ignored",     foreground=theme.COMMENT)
        self._tree.tag_configure("archived",    foreground=theme.COMMENT)

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
        state_filter  = self._show_filter.get()

        with db_session() as conn:
            rows = conn.execute(
                """
                SELECT s.trakt_id, s.title, s.show_state, s.interest_score,
                       s.trakt_status, s.primary_provider,
                       s.last_watch_date, s.next_air_date,
                       svc.name AS service_name
                FROM   shows s
                LEFT JOIN services svc ON s.service_id = svc.id
                ORDER BY s.interest_score DESC, LOWER(s.title)
                """
            ).fetchall()

        total = 0
        for row in rows:
            row = dict(row)

            if text_filter and text_filter not in row["title"].lower():
                continue

            state = row["show_state"] or "dormant"
            if state_filter != "All" and state.lower() != state_filter.lower():
                continue

            state_label, tag = _STATE_DISPLAY.get(state, (state.title(), "dormant"))
            # Archived shows get a badge suffix
            display_state = f"🗄 {state_label}" if state == "archived" else state_label

            self._tree.insert(
                "", "end",
                iid=row["trakt_id"],
                values=(
                    row["title"],
                    row["service_name"]    or "—",
                    display_state,
                    row["interest_score"]  or 0,
                    row["primary_provider"] or "—",
                    row["last_watch_date"] or "—",
                    row["next_air_date"]   or "—",
                    (row["trakt_status"] or "").replace(" series", "").title(),
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
        title  = self._tree.item(tid, "values")[0]
        action = "mark" if abandoned else "unmark"
        if messagebox.askyesno(
            "Confirm", f"{action.title()} '{title}' as abandoned?", parent=self
        ):
            with db_session() as conn:
                new_state    = "abandoned" if abandoned else "dormant"
                new_override = 1 if abandoned else 0
                conn.execute(
                    "UPDATE shows SET show_state = ?, abandoned_override = ? WHERE trakt_id = ?",
                    (new_state, new_override, tid),
                )
            self.refresh()
