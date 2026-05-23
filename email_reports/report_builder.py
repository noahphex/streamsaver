"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Builds Dracula-themed HTML digest emails. All card HTML uses inline styles
for maximum email client compatibility (Gmail, Apple Mail, Outlook).
"""

import os
from datetime import date
from typing import Literal

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "templates", "base.html"
)

# ── Dracula palette (inline style constants) ───────────────────────────────────
_FG       = "#f8f8f2"
_FG_DIM   = "#6272a4"
_GREEN    = "#50fa7b"
_ORANGE   = "#ffb86c"
_CYAN     = "#8be9fd"
_RED      = "#ff5555"
_PURPLE   = "#bd93f9"
_DIVIDER  = "#44475a"
_CARD_BG  = "#282a36"
_AVATAR_COLORS = [_PURPLE, _CYAN, _ORANGE, "#ff79c6", _GREEN, "#6272a4"]


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs) -> str:
    """Replace {{KEY}} placeholders in the template."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _logo_html(icon_path: str | None, name: str, size: int = 72) -> str:
    """
    Return inline-base64 <img> if the icon exists, otherwise a colored
    initial-letter avatar table (email-safe, no flex/grid needed).
    """
    if icon_path and os.path.exists(icon_path):
        import base64
        with open(icon_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        ext  = os.path.splitext(icon_path)[1].lstrip(".")
        mime = "image/png" if ext == "png" else f"image/{ext}"
        return (
            f'<img src="data:{mime};base64,{data}" alt="{name}" '
            f'width="{size}" height="{size}" '
            f'style="display:block;border-radius:10px;object-fit:cover;">'
        )
    # Fallback: colored square with service initial
    initial = name[0].upper() if name else "?"
    color   = _AVATAR_COLORS[hash(name) % len(_AVATAR_COLORS)]
    fsize   = size // 3
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" '
        f'style="width:{size}px;height:{size}px;border-radius:10px;background:{color};">'
        f'<tr><td align="center" valign="middle" '
        f'style="font-size:{fsize}px;font-weight:bold;color:#282a36;width:{size}px;height:{size}px;">'
        f'{initial}</td></tr></table>'
    )


def _badge(label: str, bg: str, fg: str = "#282a36") -> str:
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:11px;font-weight:bold;padding:3px 10px;border-radius:4px;'
        f'text-transform:uppercase;letter-spacing:0.5px;vertical-align:middle;'
        f'margin-left:8px;">{label}</span>'
    )


def _build_card(
    name: str,
    icon_path: str | None,
    badge: str,
    billing_line: str,
    cost_rows: list[tuple[str, str, str]],  # (icon_emoji, label, formatted_value)
    reason: str,
    extra_html: str = "",
) -> str:
    """
    Build a service card with:
      - Two-column header: info (name + badge + billing) left, logo right
      - Horizontal rule separator
      - Cost rows with dashed dividers between them
      - Optional extra HTML block (e.g. show list for resubscribe)
      - Reason footer with info icon
    """
    logo = _logo_html(icon_path, name)

    # Header: name/badge/meta left | logo right
    header = f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="middle" style="padding-right:14px;">
          <p style="color:{_FG};font-size:17px;font-weight:bold;
                    margin:0 0 8px;line-height:1.3;">
            {name}{badge}
          </p>
          <p style="color:{_FG_DIM};font-size:13px;margin:0;">
            &#128197;&nbsp; {billing_line}
          </p>
        </td>
        <td valign="middle" align="right" width="80" style="min-width:80px;">
          {logo}
        </td>
      </tr>
    </table>"""

    sep = f'<hr style="border:none;border-top:1px solid {_DIVIDER};margin:14px 0 0;">'

    # Cost rows — dashed dividers between each row
    rows_html = ""
    for i, (icon_char, label, value) in enumerate(cost_rows):
        top_border = f"border-top:1px dashed {_DIVIDER};" if i > 0 else ""
        rows_html += (
            f'<tr>'
            f'<td style="color:{_FG};font-size:14px;padding:8px 0;{top_border}">'
            f'{icon_char}&nbsp; {label}'
            f'</td>'
            f'<td align="right" style="color:{_GREEN};font-size:14px;'
            f'font-weight:bold;padding:8px 0;{top_border}">'
            f'{value}'
            f'</td>'
            f'</tr>'
        )

    costs = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{rows_html}'
        f'</table>'
        if rows_html else ""
    )

    reason_html = (
        f'<hr style="border:none;border-top:1px dashed {_DIVIDER};margin:10px 0 8px;">'
        f'<p style="color:{_FG_DIM};font-size:13px;margin:0;">'
        f'&#8505;&#xFE0F;&nbsp; {reason}'
        f'</p>'
    )

    return (
        f'<div style="background:{_CARD_BG};border:1px solid {_DIVIDER};'
        f'border-radius:8px;padding:18px 20px;margin-bottom:16px;">'
        f'{header}'
        f'{sep}'
        f'{costs}'
        f'{extra_html}'
        f'{reason_html}'
        f'</div>'
    )


# ── Suspend ────────────────────────────────────────────────────────────────────

def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider suspending: {names} before your next billing date."

    cards = []
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
            badge        = _badge("Suspend", _RED),
            billing_line = f"Next billing: {rec['billing_date']}",
            cost_rows    = [
                ("&#128178;", "Monthly savings if suspended:", f"${monthly:.2f}"),
                ("&#128202;", "Annual savings if suspended:",  f"${annual:.2f}"),
            ],
            reason = rec.get("reason", "No upcoming content before next billing date."),
        ))

    content = (
        f'<h2>&#128308; Services to Suspend</h2>'
        f'<p>The following monthly subscriptions have low engagement and no upcoming '
        f'content before the next billing date. Consider suspending to save money.</p>'
        f'{"".join(cards)}'
    )
    return content, preheader


# ── Consider non-renewal ───────────────────────────────────────────────────────

def _build_consider_non_renewal_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — low engagement detected."

    cards = []
    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")

        extra = (
            f'<p style="color:{_ORANGE};font-size:12px;margin:6px 0 0;">'
            f'&#128161; Your plan is already paid through {rec["billing_date"]}. '
            f'You can disable auto-renew now without losing access.'
            f'</p>'
        )

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge        = _badge("Consider Non-Renewal", _ORANGE),
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [
                ("&#128197;", "Annual cost:", f"${annual:.2f}"),
            ],
            reason    = rec.get("reason", "Low engagement near renewal window."),
            extra_html = extra,
        ))

    content = (
        f'<h2>&#128993; Annual Subscriptions Nearing Renewal</h2>'
        f'<p>The following annual plans are approaching renewal with low recent engagement. '
        f'Your access continues through the renewal date — consider disabling auto-renew '
        f'if you no longer find value.</p>'
        f'{"".join(cards)}'
    )
    return content, preheader


# ── Renew soon ─────────────────────────────────────────────────────────────────

def _build_renew_soon_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — looks worth keeping."

    cards = []
    for rec in recommendations:
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge        = _badge("Likely Worth Renewing", _GREEN),
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [
                ("&#128197;", "Annual cost:", f"${annual:.2f}"),
            ],
            reason = rec.get("reason", "Active engagement and upcoming content detected."),
        ))

    content = (
        f'<h2>&#128994; Annual Subscriptions Approaching Renewal</h2>'
        f'<p>The following annual plans are nearing renewal and show strong engagement '
        f'signals. These subscriptions are likely worth keeping.</p>'
        f'{"".join(cards)}'
    )
    return content, preheader


# ── Resubscribe ────────────────────────────────────────────────────────────────

def _build_resubscribe_content(recommendations: list[dict]) -> tuple[str, str]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Shows returning on: {names} — time to resubscribe!"

    cards = []
    for rec in recommendations:
        shows = rec.get("shows", [])
        show_rows = "".join(
            f'<tr>'
            f'<td style="color:{_FG};font-size:14px;padding:6px 0;'
            f'border-top:1px dashed {_DIVIDER};">&#127916;&nbsp; {s["title"]}</td>'
            f'<td align="right" style="color:{_ORANGE};font-size:13px;'
            f'padding:6px 0;border-top:1px dashed {_DIVIDER};">'
            f'{s.get("next_air_date", "—")}</td>'
            f'</tr>'
            for s in shows
        )
        extra = (
            f'<p style="color:{_FG};font-size:13px;font-weight:bold;margin:4px 0 4px;">'
            f'Upcoming shows:</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'{show_rows}'
            f'</table>'
        ) if shows else ""

        cards.append(_build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge        = _badge("Resubscribe", _GREEN) + _badge(rec["status"].title(), "#f1fa8c"),
            billing_line = f"Last billing date on file: {rec['billing_date']}",
            cost_rows    = [],
            reason       = "High-interest content is returning to this service.",
            extra_html   = extra,
        ))

    content = (
        f'<h2>&#128994; Services to Resubscribe</h2>'
        f'<p>The following suspended or unsubscribed services have high-interest shows '
        f'returning soon. Consider resubscribing.</p>'
        f'{"".join(cards)}'
    )
    return content, preheader


# ── Public API ─────────────────────────────────────────────────────────────────

def build_report(
    report_type: Literal["suspend", "consider_non_renewal", "renew_soon", "resubscribe"],
    recommendations: list[dict],
) -> str:
    """
    Build and return a complete HTML email string for a single recommendation type.
    """
    template = _load_template()

    if report_type == "suspend":
        content, preheader = _build_suspend_content(recommendations)
        title = "Streamsaver — Services to Suspend"
    elif report_type == "consider_non_renewal":
        content, preheader = _build_consider_non_renewal_content(recommendations)
        title = "Streamsaver — Annual Renewals to Review"
    elif report_type == "renew_soon":
        content, preheader = _build_renew_soon_content(recommendations)
        title = "Streamsaver — Annual Renewals Approaching"
    else:
        content, preheader = _build_resubscribe_content(recommendations)
        title = "Streamsaver — Services to Resubscribe"

    return _render(
        template,
        TITLE=title,
        PREHEADER=preheader,
        CONTENT=content,
        YEAR=str(date.today().year),
    )


def build_digest(analysis_results: dict) -> str | None:
    """
    Build a single digest email containing all actionable recommendations.
    Sections are included only when that recommendation type has entries.
    Returns None if there are no actionable recommendations.
    """
    sections      = []
    preview_parts = []

    suspend   = analysis_results.get("suspend", [])
    non_ren   = analysis_results.get("consider_non_renewal", [])
    renew     = analysis_results.get("renew_soon", [])
    resub     = analysis_results.get("resubscribe", [])
    uncertain = analysis_results.get("uncertain", [])

    if suspend:
        content, _ = _build_suspend_content(suspend)
        sections.append(content)
        preview_parts.append(f"Suspend: {', '.join(r['name'] for r in suspend)}")

    if non_ren:
        content, _ = _build_consider_non_renewal_content(non_ren)
        sections.append(content)
        preview_parts.append(f"Review renewal: {', '.join(r['name'] for r in non_ren)}")

    if renew:
        content, _ = _build_renew_soon_content(renew)
        sections.append(content)
        preview_parts.append(f"Renewing soon: {', '.join(r['name'] for r in renew)}")

    if resub:
        content, _ = _build_resubscribe_content(resub)
        sections.append(content)
        preview_parts.append(f"Resubscribe: {', '.join(r['name'] for r in resub)}")

    if uncertain:
        names     = ", ".join(r["name"] for r in uncertain)
        unc_cards = "".join(
            _build_card(
                name         = r["name"],
                icon_path    = r.get("icon_path"),
                badge        = _badge("Verify Billing", _RED, "#f8f8f2"),
                billing_line = "Billing date appears stale",
                cost_rows    = [],
                reason       = r.get("reason", "Please update the billing date in Streamsaver."),
            )
            for r in uncertain
        )
        sections.append(
            f'<h2>&#9888;&#65039; Billing Dates Need Verification</h2>'
            f'<p>The billing dates for these services appear stale. Please update them '
            f'in Streamsaver to ensure accurate recommendations.</p>'
            f'{unc_cards}'
        )
        preview_parts.append(f"Verify billing: {names}")

    if not sections:
        return None

    preheader        = " · ".join(preview_parts)
    combined_content = "\n".join(sections)

    template = _load_template()
    return _render(
        template,
        TITLE="Streamsaver — Daily Subscription Digest",
        PREHEADER=preheader,
        CONTENT=combined_content,
        YEAR=str(date.today().year),
    )
