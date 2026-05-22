"""
gui/theme.py
Streamsaver — Dracula color palette and shared style constants
Author: Noah Lee

Reference: https://draculatheme.com/
"""

# ── Dracula palette ────────────────────────────────────────────────────────────
BACKGROUND   = "#282a36"
CURRENT_LINE = "#44475a"
FOREGROUND   = "#f8f8f2"
COMMENT      = "#6272a4"
CYAN         = "#8be9fd"
GREEN        = "#50fa7b"
ORANGE       = "#ffb86c"
PINK         = "#ff79c6"
PURPLE       = "#bd93f9"
RED          = "#ff5555"
YELLOW       = "#f1fa8c"

# ── Semantic aliases ───────────────────────────────────────────────────────────
BG           = BACKGROUND
BG_ALT       = CURRENT_LINE
FG           = FOREGROUND
FG_DIM       = COMMENT
ACCENT       = PURPLE
SUCCESS      = GREEN
WARNING      = ORANGE
DANGER       = RED
INFO         = CYAN
HIGHLIGHT    = YELLOW

# ── Status badge colors ────────────────────────────────────────────────────────
STATUS_COLORS = {
    "subscribed":     GREEN,
    "suspended":      YELLOW,
    "unsubscribed":   ORANGE,
    "not_subscribed": COMMENT,
}

STATUS_LABELS = {
    "subscribed":     "Subscribed",
    "suspended":      "Suspended",
    "unsubscribed":   "Unsubscribed",
    "not_subscribed": "Not Subscribed",
}

# ── Fonts ──────────────────────────────────────────────────────────────────────
FONT_FAMILY  = "Helvetica"
FONT_NORMAL  = (FONT_FAMILY, 11)
FONT_BOLD    = (FONT_FAMILY, 11, "bold")
FONT_HEADING = (FONT_FAMILY, 14, "bold")
FONT_TITLE   = (FONT_FAMILY, 18, "bold")
FONT_SMALL   = (FONT_FAMILY, 9)
FONT_MONO    = ("Courier", 10)

# ── Widget geometry ────────────────────────────────────────────────────────────
PAD          = 10
PAD_SMALL    = 5
BUTTON_H     = 32
ROW_H        = 28
