"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Builds Dracula-themed digest emails using strict <table> layout.
Compatible with: Gmail, Apple Mail, Outlook desktop/web, iOS Mail.
No flexbox, no CSS grid, no CSS variables, no external fonts.

Card structure per recommendation:
  HEADER ROW   — service name + badge (78%) | logo block (22%)
  DIVIDER      — solid 1px separator
  METRICS      — 2-column rows (label | value) with dashed dividers
  REASON ROW   — muted explanation text
"""

import os
import base64
from datetime import date
from typing import Literal

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "base.html")

# ── Color palette ─────────────────────────────────────────────────────────────
_FG        = "#f8f8f2"   # primary text
_FG_DIM    = "#9aa3c7"   # muted / reason text
_FG_META   = "#6272a4"   # billing date / metadata
_GREEN     = "#50fa7b"
_ORANGE    = "#ffb86c"
_RED       = "#ff5a5f"
_BLUE      = "#8be9fd"
_PURPLE    = "#bd93f9"
_AMBER     = "#ffb86c"
_DIVIDER   = "#3a3d52"
_CARD_BG   = "#2f3447"
_LOGO_BG   = "#1c1f2b"

# Avatar fallback colors keyed by service name hash
_AVATAR_COLORS = ["#bd93f9", "#8be9fd", "#ffb86c", "#ff79c6", "#50fa7b", "#6272a4"]

# Semantic badge colors per recommendation type
_BADGE_COLORS = {
    "suspend":              (_RED,    "#ffffff"),
    "consider_non_renewal": (_AMBER,  "#1c1f2b"),
    "renew_soon":           (_BLUE,   "#1c1f2b"),
    "resubscribe":          (_PURPLE, "#ffffff"),
    "uncertain":            (_RED,    "#ffffff"),
}


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _logo_html(icon_path: str | None, name: str, size: int = 72) -> str:
    """
    Returns an email-safe logo block:
    - Inline base64 <img> if icon file exists on disk.
    - Colored initial-letter avatar table as fallback.
    Logo is wrapped in a dark bordered box matching the spec.
    """
    inner_size = size - 20  # 10px padding each side

    if icon_path and os.path.exists(icon_path):
        with open(icon_path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        ext  = os.path.splitext(icon_path)[1].lstrip(".").lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        img  = (
            f'<img src="data:{mime};base64,{data}" alt="" border="0" '
            f'width="{inner_size}" height="{inner_size}" '
            f'style="display:block;border-radius:6px;object-fit:contain;">'
        )
    else:
        initial = name[0].upper() if name else "?"
        color   = _AVATAR_COLORS[hash(name) % len(_AVATAR_COLORS)]
        fsize   = inner_size // 3
        img = (
            f'<table width="{inner_size}" height="{inner_size}" cellpadding="0" '
            f'cellspacing="0" border="0" bgcolor="{color}" '
            f'style="background-color:{color};border-radius:6px;">'
            f'<tr><td width="{inner_size}" height="{inner_size}" align="center" '
            f'valign="middle" style="font-size:{fsize}px;font-weight:700;'
            f'color:#1c1f2b;font-family:Arial,Helvetica,sans-serif;">'
            f'{initial}</td></tr></table>'
        )

    # Dark bordered container box
    return (
        f'<table width="{size}" height="{size}" cellpadding="10" cellspacing="0" '
        f'border="0" bgcolor="{_LOGO_BG}" '
        f'style="background-color:{_LOGO_BG};border-radius:14px;'
        f'border:1px solid {_DIVIDER};width:{size}px;height:{size}px;">'
        f'<tr><td align="center" valign="middle">{img}</td></tr></table>'
    )


def _badge(label: str, bg: str, fg: str) -> str:
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:11px;font-weight:700;letter-spacing:0.6px;'
        f'padding:6px 12px;border-radius:8px;font-family:Arial,Helvetica,sans-serif;'
        f'text-transform:uppercase;">{label}</span>'
    )


def _build_card(
    name: str,
    icon_path: str | None,
    badge_label: str,
    badge_bg: str,
    badge_fg: str,
    billing_line: str,
    cost_rows: list[tuple[str, str]],   # (label, value)
    reason: str,
    extra_html: str = "",
) -> str:
    """
    Strict 3-section email-safe card:
      HEADER  — 78% info | 22% logo
      METRICS — 2-col rows with dashed dividers
      REASON  — muted footer text
    """
    logo = _logo_html(icon_path, name)

    # ── HEADER ROW: 78% info | 22% logo ─────────────────────────────────────
    header = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'role="presentation">'
        f'<tr>'
        # Info cell — owns 78% of width
        f'<td width="78%" valign="top" '
        f'style="padding:0 16px 0 0;vertical-align:top;">'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;'
        f'font-weight:700;color:{_FG};margin:0 0 8px;line-height:1.3;">'
        f'{name}</p>'
        f'<p style="margin:0 0 10px;">{_badge(badge_label, badge_bg, badge_fg)}</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:500;color:{_FG_META};margin:0;line-height:1.4;">'
        f'{billing_line}</p>'
        f'</td>'
        # Logo cell — fixed 22%, right-aligned
        f'<td width="22%" valign="top" align="right" '
        f'style="vertical-align:top;">'
        f'{logo}'
        f'</td>'
        f'</tr></table>'
    )

    # ── SOLID DIVIDER ────────────────────────────────────────────────────────
    divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:18px 0 0;">'
        f'<tr><td height="1" bgcolor="{_DIVIDER}" '
        f'style="font-size:0;line-height:0;background-color:{_DIVIDER};"></td></tr>'
        f'</table>'
    )

    # ── METRICS ROWS: 2-col, dashed dividers between rows ────────────────────
    rows_html = ""
    for i, (label, value) in enumerate(cost_rows):
        border = f"border-top:1px dashed {_DIVIDER};" if i > 0 else ""
        rows_html += (
            f'<tr>'
            f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'font-weight:600;color:{_FG};padding:12px 0;{border}">{label}</td>'
            f'<td align="right" style="font-family:Arial,Helvetica,sans-serif;'
            f'font-size:15px;font-weight:700;color:{_GREEN};padding:12px 0;'
            f'white-space:nowrap;{border}">{value}</td>'
            f'</tr>'
        )
    metrics = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{rows_html}</table>'
        if rows_html else ""
    )

    # ── REASON FOOTER ────────────────────────────────────────────────────────
    reason_divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:8px 0 10px;">'
        f'<tr><td style="font-size:0;line-height:0;'
        f'border-top:1px dashed {_DIVIDER};"></td></tr></table>'
    )
    reason_block = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:400;color:{_FG_DIM};margin:0;line-height:20px;">'
        f'{reason}</p>'
    )

    return (
        f'<table width="100%" cellpadding="20" cellspacing="0" border="0" '
        f'bgcolor="{_CARD_BG}" style="background-color:{_CARD_BG};'
        f'border-radius:10px;border:1px solid {_DIVIDER};'
        f'margin-bottom:16px;">'
        f'<tr><td style="padding:20px;">'
        f'{header}{divider}{metrics}{extra_html}{reason_divider}{reason_block}'
        f'</td></tr></table>'
    )


# ── Section builders ───────────────────────────────────────────────────────────

def _section_header(title: str, subtitle: str) -> str:
    return (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:18px;'
        f'font-weight:700;color:{_FG};margin:0 0 6px;">{title}</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:400;color:{_FG_DIM};margin:0 0 16px;line-height:20px;">'
        f'{subtitle}</p>'
    )


def _section_wrap(content: str) -> str:
    """Add top padding/border between sections."""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="padding-top:18px;margin-top:18px;border-top:1px solid {_DIVIDER};">'
        f'<tr><td>{content}</td></tr></table>'
    )


def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider pausing: {names} before your next billing date."
    bg, fg    = _BADGE_COLORS["suspend"]
    cards     = []

    for rec in recommendations:
        monthly = float(rec.get("estimated_monthly_spend") or rec.get("monthly_cost") or 0.0)
        annual  = float(rec.get("estimated_annual_spend")  or rec.get("annual_cost")  or 0.0)
        if monthly and not annual:
            annual = round(monthly * 12, 2)
        elif annual and not monthly:
            monthly = round(annual / 12, 2)

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Suspend",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Next billing: {rec['billing_date']}",
            cost_rows    = [
                ("Monthly savings if paused", f"${monthly:.2f}"),
                ("Annual savings if paused",  f"${annual:.2f}"),
            ],
            reason = rec.get("reason", "No upcoming content before next billing date."),
        ))

    content = (
        _section_header("Services to Pause", "Low activity services that may be safe to suspend.")
        + "".join(cards)
    )
    return content, preheader


def _build_consider_non_renewal_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — low engagement detected."
    bg, fg    = _BADGE_COLORS["consider_non_renewal"]
    cards     = []

    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        extra = (
            f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'color:{_ORANGE};margin:8px 0 0;line-height:18px;">'
            f'Your plan is paid through {rec["billing_date"]}. '
            f'You can disable auto-renew without losing access.</p>'
        )
        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Consider Non-Renewal",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost", f"${annual:.2f}")],
            reason       = rec.get("reason", "Low engagement near renewal window."),
            extra_html   = extra,
        ))

    content = (
        _section_header(
            "Annual Renewals to Review",
            "Low engagement near renewal — consider disabling auto-renew.",
        )
        + "".join(cards)
    )
    return content, preheader


def _build_renew_soon_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — looks worth keeping."
    bg, fg    = _BADGE_COLORS["renew_soon"]
    cards     = []

    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Likely Worth Renewing",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost", f"${annual:.2f}")],
            reason       = rec.get("reason", "Active engagement and upcoming content detected."),
        ))

    content = (
        _section_header(
            "Annual Renewals Approaching",
            "Strong engagement signals — these subscriptions look worth keeping.",
        )
        + "".join(cards)
    )
    return content, preheader


def _build_resubscribe_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Shows returning on {names} — time to resubscribe!"
    bg, fg    = _BADGE_COLORS["resubscribe"]
    cards     = []

    for rec in recommendations:
        shows = rec.get("shows", [])
        show_rows = "".join(
            f'<tr>'
            f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'color:{_FG};padding:8px 0;border-top:1px dashed {_DIVIDER};">'
            f'{s["title"]}</td>'
            f'<td align="right" style="font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;color:{_ORANGE};padding:8px 0;white-space:nowrap;'
            f'border-top:1px dashed {_DIVIDER};">'
            f'{s.get("next_air_date", "—")}</td>'
            f'</tr>'
            for s in shows
        )
        extra = (
            f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'font-weight:600;color:{_FG};margin:10px 0 6px;">Upcoming shows</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{show_rows}</table>'
        ) if shows else ""

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Resubscribe",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Last billing date on file: {rec['billing_date']}",
            cost_rows    = [],
            reason       = "High-interest content is returning to this service.",
            extra_html   = extra,
        ))

    content = (
        _section_header(
            "Services to Resubscribe",
            "Previously paused services with high-interest content returning soon.",
        )
        + "".join(cards)
    )
    return content, preheader


# ── Public API ─────────────────────────────────────────────────────────────────

def build_report(
    report_type: Literal["suspend", "consider_non_renewal", "renew_soon", "resubscribe"],
    recommendations: list[dict],
) -> str:
    """Build a single-type HTML email report."""
    dispatch = {
        "suspend":              (_build_suspend_content,              "Streamsaver — Services to Pause"),
        "consider_non_renewal": (_build_consider_non_renewal_content, "Streamsaver — Annual Renewals to Review"),
        "renew_soon":           (_build_renew_soon_content,           "Streamsaver — Annual Renewals Approaching"),
        "resubscribe":          (_build_resubscribe_content,          "Streamsaver — Services to Resubscribe"),
    }
    builder, title = dispatch.get(report_type, dispatch["resubscribe"])
    content, preheader = builder(recommendations)
    return _render(
        _load_template(),
        TITLE=title, PREHEADER=preheader, CONTENT=content, YEAR=str(date.today().year),
    )


def build_digest(analysis_results: dict) -> str | None:
    """
    Build a single digest email covering all actionable recommendation types.
    Returns None if there is nothing to report.
    """
    sections      = []
    preview_parts = []

    _SECTIONS = [
        ("suspend",              _build_suspend_content,              "Pause"),
        ("consider_non_renewal", _build_consider_non_renewal_content, "Review renewal"),
        ("renew_soon",           _build_renew_soon_content,           "Renewing soon"),
        ("resubscribe",          _build_resubscribe_content,          "Resubscribe"),
    ]

    for key, builder, prefix in _SECTIONS:
        recs = analysis_results.get(key, [])
        if recs:
            content, _ = builder(recs)
            sections.append(content if not sections else _section_wrap(content))
            preview_parts.append(f"{prefix}: {', '.join(r['name'] for r in recs)}")

    uncertain = analysis_results.get("uncertain", [])
    if uncertain:
        bg, fg = _BADGE_COLORS["uncertain"]
        unc_cards = "".join(
            _build_card(
                name         = r["name"],
                icon_path    = r.get("icon_path"),
                badge_label  = "Verify Billing",
                badge_bg     = bg,
                badge_fg     = fg,
                billing_line = "Billing date appears stale",
                cost_rows    = [],
                reason       = r.get("reason", "Please update the billing date in Streamsaver."),
            )
            for r in uncertain
        )
        unc_content = (
            _section_header(
                "Billing Dates Need Verification",
                "These billing dates appear stale — update them in Streamsaver.",
            )
            + unc_cards
        )
        sections.append(unc_content if not sections else _section_wrap(unc_content))
        preview_parts.append(f"Verify billing: {', '.join(r['name'] for r in uncertain)}")

    if not sections:
        return None

    return _render(
        _load_template(),
        TITLE="Streamsaver — Daily Subscription Digest",
        PREHEADER=" · ".join(preview_parts),
        CONTENT="\n".join(sections),
        YEAR=str(date.today().year),
    )
