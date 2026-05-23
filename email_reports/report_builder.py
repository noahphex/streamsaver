"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Single digest email with all actionable recommendation sections.
All layout uses <table> elements with HTML attributes for email client compatibility.
No emoji or CSS-only features — ensures correct rendering in Gmail, Apple Mail, Outlook.
"""

import os
from datetime import date
from typing import Literal

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "base.html")

_FG      = "#f8f8f2"
_FG_DIM  = "#6272a4"
_GREEN   = "#50fa7b"
_ORANGE  = "#ffb86c"
_RED     = "#ff5555"
_DIVIDER = "#44475a"
_CARD_BG = "#282a36"
_AVATAR_COLORS = ["#bd93f9", "#8be9fd", "#ffb86c", "#ff79c6", "#50fa7b", "#6272a4"]


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _logo_html(icon_path: str | None, name: str, size: int = 72) -> str:
    """
    Inline-base64 <img> if icon exists; otherwise a colored initial-letter square.
    Uses HTML attributes (width/height/bgcolor) for email client compatibility.
    """
    if icon_path and os.path.exists(icon_path):
        import base64
        with open(icon_path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        ext  = os.path.splitext(icon_path)[1].lstrip(".")
        mime = "image/png" if ext == "png" else f"image/{ext}"
        return (
            f'<img src="data:{mime};base64,{data}" alt="" border="0" '
            f'width="{size}" height="{size}" '
            f'style="display:block;border-radius:10px;">'
        )
    initial = name[0].upper() if name else "?"
    color   = _AVATAR_COLORS[hash(name) % len(_AVATAR_COLORS)]
    fsize   = size // 3
    # bgcolor + width/height as HTML attributes ensure rendering in all email clients
    return (
        f'<table width="{size}" height="{size}" cellpadding="0" cellspacing="0" '
        f'border="0" bgcolor="{color}" '
        f'style="background-color:{color};border-radius:10px;">'
        f'<tr><td width="{size}" height="{size}" align="center" valign="middle" '
        f'style="font-size:{fsize}px;font-weight:bold;color:#282a36;'
        f'font-family:Arial,sans-serif;text-align:center;">'
        f'{initial}</td></tr></table>'
    )


def _badge(label: str, bg: str, fg: str = "#282a36") -> str:
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:11px;font-weight:bold;padding:3px 10px;border-radius:4px;'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-left:8px;">{label}</span>'
    )


def _build_card(
    name: str,
    icon_path: str | None,
    badge_html: str,
    billing_line: str,
    cost_rows: list[tuple[str, str]],   # (label, value)
    reason: str,
    extra_html: str = "",
) -> str:
    """
    Email-safe service card using nested <table> elements.
    Header: name/badge/billing date left, logo right.
    Body: plain 2-column cost rows, optional extra block, reason footer.
    No emoji — avoids rendering inconsistencies across email clients.
    """
    logo = _logo_html(icon_path, name)

    # Two-column header: info left, logo right
    header = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr>'
        f'<td valign="top" style="padding-right:16px;">'
        f'<p style="margin:0 0 8px;color:{_FG};font-size:17px;font-weight:bold;'
        f'font-family:Arial,sans-serif;line-height:1.3;">{name}{badge_html}</p>'
        f'<p style="margin:0;color:{_FG_DIM};font-size:13px;'
        f'font-family:Arial,sans-serif;">{billing_line}</p>'
        f'</td>'
        f'<td valign="top" align="right" width="{72}" style="width:72px;">'
        f'{logo}'
        f'</td>'
        f'</tr></table>'
    )

    # Solid divider after header
    divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:14px 0 0;">'
        f'<tr><td height="1" bgcolor="{_DIVIDER}" '
        f'style="font-size:0;line-height:0;background-color:{_DIVIDER};">'
        f'</td></tr></table>'
    )

    # Cost rows: plain 2-column (label | value), dashed dividers between rows
    rows_html = ""
    for i, (label, value) in enumerate(cost_rows):
        border = f"border-top:1px dashed {_DIVIDER};" if i > 0 else ""
        rows_html += (
            f'<tr>'
            f'<td style="color:{_FG};font-size:14px;font-family:Arial,sans-serif;'
            f'padding:8px 0;{border}">{label}</td>'
            f'<td align="right" style="color:{_GREEN};font-size:14px;font-weight:bold;'
            f'font-family:Arial,sans-serif;padding:8px 0;white-space:nowrap;{border}">'
            f'{value}</td>'
            f'</tr>'
        )
    costs = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{rows_html}</table>'
        if rows_html else ""
    )

    # Dashed divider before reason footer
    reason_divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:10px 0 6px;">'
        f'<tr><td height="1" style="font-size:0;line-height:0;'
        f'border-top:1px dashed {_DIVIDER};"></td></tr></table>'
    )
    reason_block = (
        f'<p style="color:{_FG_DIM};font-size:13px;font-family:Arial,sans-serif;'
        f'margin:0;line-height:1.5;">{reason}</p>'
    )

    return (
        f'<div style="background:{_CARD_BG};border:1px solid {_DIVIDER};'
        f'border-radius:8px;padding:18px 20px;margin-bottom:16px;">'
        f'{header}{divider}{costs}{extra_html}{reason_divider}{reason_block}'
        f'</div>'
    )


# ── Section builders ───────────────────────────────────────────────────────────

def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider suspending: {names} before your next billing date."
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
            badge_html   = _badge("Suspend", _RED),
            billing_line = f"Next billing: {rec['billing_date']}",
            cost_rows    = [
                ("Monthly savings if suspended:", f"${monthly:.2f}"),
                ("Annual savings if suspended:",  f"${annual:.2f}"),
            ],
            reason = rec.get("reason", "No upcoming content before next billing date."),
        ))

    return (
        f'<h2 style="color:{_FG};font-family:Arial,sans-serif;">Services to Suspend</h2>'
        f'<p style="color:{_FG_DIM};font-family:Arial,sans-serif;">The following monthly '
        f'subscriptions have low engagement and no upcoming content before the next billing '
        f'date. Consider suspending to save money.</p>'
        + "".join(cards),
        preheader,
    )


def _build_consider_non_renewal_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — low engagement detected."
    cards     = []

    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        extra = (
            f'<p style="color:{_ORANGE};font-size:12px;font-family:Arial,sans-serif;'
            f'margin:6px 0 0;">Your plan is already paid through {rec["billing_date"]}. '
            f'You can disable auto-renew now without losing access.</p>'
        )
        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_html   = _badge("Consider Non-Renewal", _ORANGE),
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost:", f"${annual:.2f}")],
            reason       = rec.get("reason", "Low engagement near renewal window."),
            extra_html   = extra,
        ))

    return (
        f'<h2 style="color:{_FG};font-family:Arial,sans-serif;">Annual Subscriptions Nearing Renewal</h2>'
        f'<p style="color:{_FG_DIM};font-family:Arial,sans-serif;">The following annual plans are '
        f'approaching renewal with low recent engagement. Consider disabling auto-renew if you '
        f'no longer find value.</p>'
        + "".join(cards),
        preheader,
    )


def _build_renew_soon_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — looks worth keeping."
    cards     = []

    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_html   = _badge("Likely Worth Renewing", _GREEN),
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost:", f"${annual:.2f}")],
            reason       = rec.get("reason", "Active engagement and upcoming content detected."),
        ))

    return (
        f'<h2 style="color:{_FG};font-family:Arial,sans-serif;">Annual Subscriptions Approaching Renewal</h2>'
        f'<p style="color:{_FG_DIM};font-family:Arial,sans-serif;">The following annual plans are '
        f'nearing renewal and show strong engagement signals. These subscriptions are likely '
        f'worth keeping.</p>'
        + "".join(cards),
        preheader,
    )


def _build_resubscribe_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Shows returning on: {names} — time to resubscribe!"
    cards     = []

    for rec in recommendations:
        shows = rec.get("shows", [])
        show_rows = "".join(
            f'<tr>'
            f'<td style="color:{_FG};font-size:14px;font-family:Arial,sans-serif;'
            f'padding:6px 0;border-top:1px dashed {_DIVIDER};">{s["title"]}</td>'
            f'<td align="right" style="color:{_ORANGE};font-size:13px;white-space:nowrap;'
            f'font-family:Arial,sans-serif;padding:6px 0;border-top:1px dashed {_DIVIDER};">'
            f'{s.get("next_air_date", "—")}</td>'
            f'</tr>'
            for s in shows
        )
        extra = (
            f'<p style="color:{_FG};font-size:13px;font-weight:bold;'
            f'font-family:Arial,sans-serif;margin:8px 0 4px;">Upcoming shows:</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{show_rows}</table>'
        ) if shows else ""

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_html   = _badge("Resubscribe", _GREEN) + _badge(rec["status"].title(), "#f1fa8c"),
            billing_line = f"Last billing date on file: {rec['billing_date']}",
            cost_rows    = [],
            reason       = "High-interest content is returning to this service.",
            extra_html   = extra,
        ))

    return (
        f'<h2 style="color:{_FG};font-family:Arial,sans-serif;">Services to Resubscribe</h2>'
        f'<p style="color:{_FG_DIM};font-family:Arial,sans-serif;">The following suspended or '
        f'unsubscribed services have high-interest shows returning soon. Consider resubscribing.</p>'
        + "".join(cards),
        preheader,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def build_report(
    report_type: Literal["suspend", "consider_non_renewal", "renew_soon", "resubscribe"],
    recommendations: list[dict],
) -> str:
    """Build a single-type HTML email report."""
    dispatch = {
        "suspend":              (_build_suspend_content,              "Streamsaver — Services to Suspend"),
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
    Build a single digest email with all actionable sections.
    Returns None if there is nothing to report.
    """
    sections      = []
    preview_parts = []

    _SECTIONS = [
        ("suspend",              _build_suspend_content,              "Suspend"),
        ("consider_non_renewal", _build_consider_non_renewal_content, "Review renewal"),
        ("renew_soon",           _build_renew_soon_content,           "Renewing soon"),
        ("resubscribe",          _build_resubscribe_content,          "Resubscribe"),
    ]

    for key, builder, prefix in _SECTIONS:
        recs = analysis_results.get(key, [])
        if recs:
            content, _ = builder(recs)
            sections.append(content)
            preview_parts.append(f"{prefix}: {', '.join(r['name'] for r in recs)}")

    uncertain = analysis_results.get("uncertain", [])
    if uncertain:
        unc_cards = "".join(
            _build_card(
                name         = r["name"],
                icon_path    = r.get("icon_path"),
                badge_html   = _badge("Verify Billing", _RED, "#f8f8f2"),
                billing_line = "Billing date appears stale",
                cost_rows    = [],
                reason       = r.get("reason", "Please update the billing date in Streamsaver."),
            )
            for r in uncertain
        )
        sections.append(
            f'<h2 style="color:{_FG};font-family:Arial,sans-serif;">Billing Dates Need Verification</h2>'
            f'<p style="color:{_FG_DIM};font-family:Arial,sans-serif;">The billing dates for these '
            f'services appear stale. Please update them in Streamsaver to ensure accurate '
            f'recommendations.</p>'
            + unc_cards
        )
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
