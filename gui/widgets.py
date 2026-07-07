"""
gui/widgets.py
Streamsaver — Reusable themed Tkinter widgets
Author: Noah Lee
"""

from tkinter import ttk
from gui import theme


def configure_ttk_styles() -> None:
    """Apply Dracula-themed ttk styles to the current Tk instance."""
    style = ttk.Style()
    style.theme_use("clam")

    # ── General ────────────────────────────────────────────────────────────
    style.configure(
        ".",
        background=theme.BG,
        foreground=theme.FG,
        fieldbackground=theme.BG_ALT,
        troughcolor=theme.BG_ALT,
        bordercolor=theme.BG_ALT,
        darkcolor=theme.BG,
        lightcolor=theme.BG,
        relief="flat",
        font=theme.FONT_NORMAL,
    )

    # ── Frame ──────────────────────────────────────────────────────────────
    style.configure("TFrame", background=theme.BG)
    style.configure("Alt.TFrame", background=theme.BG_ALT)

    # ── Label ──────────────────────────────────────────────────────────────
    style.configure("TLabel", background=theme.BG, foreground=theme.FG)
    style.configure("Heading.TLabel",  background=theme.BG, foreground=theme.ACCENT,  font=theme.FONT_HEADING)
    style.configure("Title.TLabel",    background=theme.BG, foreground=theme.PURPLE,  font=theme.FONT_TITLE)
    style.configure("Dim.TLabel",      background=theme.BG, foreground=theme.FG_DIM)
    style.configure("Success.TLabel",  background=theme.BG, foreground=theme.SUCCESS)
    style.configure("Warning.TLabel",  background=theme.BG, foreground=theme.WARNING)
    style.configure("Danger.TLabel",   background=theme.BG, foreground=theme.DANGER)
    style.configure("Info.TLabel",     background=theme.BG, foreground=theme.INFO)

    # ── Button ─────────────────────────────────────────────────────────────
    style.configure(
        "TButton",
        background=theme.BG_ALT,
        foreground=theme.FG,
        borderwidth=0,
        padding=(10, 6),
        font=theme.FONT_NORMAL,
    )
    style.map("TButton",
              background=[("active", theme.ACCENT), ("pressed", theme.PURPLE)],
              foreground=[("active", theme.BG)])

    style.configure("Accent.TButton", background=theme.ACCENT, foreground=theme.BG)
    style.map("Accent.TButton",
              background=[("active", theme.PURPLE)],
              foreground=[("active", theme.BG)])

    style.configure("Danger.TButton", background=theme.DANGER, foreground=theme.FG)
    style.map("Danger.TButton",
              background=[("active", theme.RED)],
              foreground=[("active", theme.BG)])

    # ── Notebook (tabs) ────────────────────────────────────────────────────
    style.configure(
        "TNotebook",
        background=theme.BG,
        borderwidth=0,
        tabmargins=[0, 0, 0, 0],
    )
    style.configure(
        "TNotebook.Tab",
        background=theme.BG_ALT,
        foreground=theme.FG_DIM,
        padding=(14, 8),
        font=theme.FONT_BOLD,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme.BG)],
        foreground=[("selected", theme.ACCENT)],
    )

    # ── Treeview ───────────────────────────────────────────────────────────
    style.configure(
        "Treeview",
        background=theme.BG_ALT,
        foreground=theme.FG,
        fieldbackground=theme.BG_ALT,
        borderwidth=0,
        rowheight=theme.ROW_H,
        font=theme.FONT_NORMAL,
    )
    style.configure(
        "Treeview.Heading",
        background=theme.BG,
        foreground=theme.ACCENT,
        font=theme.FONT_BOLD,
        borderwidth=0,
        relief="flat",
    )
    style.map("Treeview",
              background=[("selected", theme.ACCENT)],
              foreground=[("selected", theme.BG)])

    # ── Entry ──────────────────────────────────────────────────────────────
    style.configure(
        "TEntry",
        fieldbackground=theme.BG_ALT,
        foreground=theme.FG,
        insertcolor=theme.FG,
        borderwidth=1,
        relief="flat",
        padding=(6, 4),
    )

    # ── Combobox ───────────────────────────────────────────────────────────
    style.configure(
        "TCombobox",
        fieldbackground=theme.BG_ALT,
        background=theme.BG_ALT,
        foreground=theme.FG,
        selectbackground=theme.ACCENT,
        selectforeground=theme.BG,
        arrowcolor=theme.ACCENT,
    )

    # ── Scrollbar ──────────────────────────────────────────────────────────
    style.configure(
        "TScrollbar",
        background=theme.BG_ALT,
        troughcolor=theme.BG,
        arrowcolor=theme.ACCENT,
        borderwidth=0,
    )
    style.map("TScrollbar", background=[("active", theme.ACCENT)])

    # ── Checkbutton ────────────────────────────────────────────────────────
    style.configure(
        "TCheckbutton",
        background=theme.BG,
        foreground=theme.FG,
        indicatorcolor=theme.BG_ALT,
        indicatorrelief="flat",
    )
    style.map("TCheckbutton",
              indicatorcolor=[("selected", theme.ACCENT)])

    # ── Separator ──────────────────────────────────────────────────────────
    style.configure("TSeparator", background=theme.BG_ALT)

    # ── Progressbar ────────────────────────────────────────────────────────
    style.configure(
        "TProgressbar",
        troughcolor=theme.BG_ALT,
        background=theme.ACCENT,
        borderwidth=0,
    )


class SectionHeader(ttk.Label):
    """Bold accent-colored section heading."""

    def __init__(self, parent, text: str, **kwargs):
        super().__init__(parent, text=text, style="Heading.TLabel", **kwargs)
