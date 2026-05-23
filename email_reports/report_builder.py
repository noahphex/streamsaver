"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Builds Dracula-themed digest emails using strict <table> layout.
Compatible with: Gmail, Apple Mail, Outlook desktop/web, iOS Mail.
No flexbox, no CSS grid, no CSS variables, no external fonts.

Public API:
  build_digest(analysis_results) → (html, attachments) | None
  build_report(report_type, recs) → (html, attachments)

attachments: dict[cid_str, MIMEImage] — deduplicated, ready to embed.
"""

import os
from datetime import date
from email.mime.image import MIMEImage
from typing import Literal

from email_reports.logo import get_service_logo_cid

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "base.html")

# ── Header logo ────────────────────────────────────────────────────────────────
_HEADER_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "logos", "streamsaver_header.jpg",
)
_HEADER_LOGO_CID = "streamsaver_header"

# Fallback when the image file is missing or unreadable
_HEADER_TEXT_FALLBACK = (
    '<p style="font-family:Arial,Helvetica,sans-serif;font-size:36px;'
    'font-weight:700;color:#bd93f9;letter-spacing:-0.5px;margin:0;">'
    'Streamsaver</p>'
    '<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
    'color:#6272a4;margin-top:8px;margin-bottom:0;">'
    'Streaming cost intelligence</p>'
)


def _get_header_logo() -> tuple[str, str | None, MIMEImage | None]:
    """
    Load streamsaver_header.jpg for CID embedding.
    Returns (img_html, cid, mime_part).
    Falls back to styled text if the file is missing.
    """
    if os.path.exists(_HEADER_LOGO_PATH):
        try:
            with open(_HEADER_LOGO_PATH, "rb") as fh:
                raw = fh.read()
            if raw:
                mime_part = MIMEImage(raw, _subtype="jpeg")
                mime_part.add_header("Content-ID", f"<{_HEADER_LOGO_CID}>")
                mime_part.add_header("Content-Disposition", "inline",
                                     filename="streamsaver_header.jpg")
                img_html = (
                    f'<img src="cid:{_HEADER_LOGO_CID}" '
                    f'alt="Streamsaver — Streaming Cost Intelligence" '
                    f'border="0" width="320" '
                    f'style="display:block;max-width:320px;height:auto;margin:0 auto;">'
                )
                return img_html, _HEADER_LOGO_CID, mime_part
        except OSError:
            pass
    return _HEADER_TEXT_FALLBACK, None, None

# ── Color palette ─────────────────────────────────────────────────────────────
_FG      = "#f8f8f2"
_FG_DIM  = "#9aa3c7"
_FG_META = "#6272a4"
_GREEN   = "#62e58c"
_ORANGE  = "#ffb86c"
_RED     = "#ff5a5f"
_BLUE    = "#8be9fd"
_PURPLE  = "#bd93f9"
_AMBER   = "#ffb86c"
_DIVIDER = "#3a3d52"
_ROW_SEP = "rgba(255,255,255,0.06)"
_CARD_BG = "#31364a"

_BADGE_COLORS = {
    "suspend":              ("#ff6b6b", "#ffffff"),
    "consider_non_renewal": (_AMBER,    "#1c1f2b"),
    "renew_soon":           (_BLUE,     "#1c1f2b"),
    "resubscribe":          (_PURPLE,   "#ffffff"),
    "uncertain":            ("#ff6b6b", "#ffffff"),
}

_ACCENT_COLORS = {
    "suspend":              "#ff6b6b",
    "consider_non_renewal": _AMBER,
    "renew_soon":           _BLUE,
    "resubscribe":          _PURPLE,
    "uncertain":            "#ff6b6b",
}

# Type alias for the attachments dict
Attachments = dict[str, MIMEImage]


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


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
    cost_rows: list[tuple[str, str]],
    reason: str,
    extra_html: str = "",
    accent_color: str = "",
    confidence: str = "",
    is_top: bool = False,
) -> tuple[str, Attachments]:
    """
    3-section email-safe card. Returns (html, attachments).
    attachments: {cid: MIMEImage} — empty dict if logo unavailable (avatar used).
    """
    logo_html, cid, mime_part = get_service_logo_cid({"name": name, "icon_path": icon_path})
    attachments: Attachments = {cid: mime_part} if cid and mime_part else {}

    # ── HEADER: name / badge / billing date (left) | logo top-aligned (right) ──
    billing_cell = (
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin:0;">'
        f'<tr>'
        f'<td width="18" valign="middle" style="vertical-align:middle;padding-right:4px;'
        f'font-size:13px;line-height:1;color:{_FG_META};">📅</td>'
        f'<td valign="middle" style="vertical-align:middle;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:500;color:{_FG_META};line-height:1.4;">'
        f'{billing_line}</td>'
        f'</tr></table>'
    )
    header = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'role="presentation">'
        f'<tr>'
        f'<td class="ss-info-col" valign="top" '
        f'style="padding:0 16px 0 0;vertical-align:top;">'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;'
        f'font-weight:700;color:{_FG};margin:0 0 10px;line-height:1.3;">'
        f'{name}</p>'
        f'<p style="margin:0 0 8px;">{_badge(badge_label, badge_bg, badge_fg)}</p>'
        + (
            f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
            f'font-weight:500;color:{_FG_META};margin:0 0 12px;line-height:1.3;">'
            f'Confidence: {confidence}</p>'
            if confidence else
            f'<p style="margin:0 0 12px;"></p>'
        ) +
        f'{billing_cell}'
        f'</td>'
        f'<td class="ss-logo-col" valign="top" align="right" width="72" '
        f'style="vertical-align:top;padding-top:4px;width:72px;">'
        f'{logo_html}'
        f'</td>'
        f'</tr></table>'
    )

    # ── DIVIDER ───────────────────────────────────────────────────────────────
    divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:20px 0 0;">'
        f'<tr><td height="1" bgcolor="{_DIVIDER}" '
        f'style="font-size:0;line-height:0;background-color:{_DIVIDER};"></td></tr>'
        f'</table>'
    )

    # ── METRICS ───────────────────────────────────────────────────────────────
    rows_html = ""
    for i, (label, value) in enumerate(cost_rows):
        border = f"border-top:1px solid {_ROW_SEP};" if i > 0 else ""
        rows_html += (
            f'<tr>'
            f'<td valign="middle" style="padding:12px 0;{border}">'
            f'<table cellpadding="0" cellspacing="0" border="0" style="margin:0;">'
            f'<tr>'
            f'<td width="18" valign="middle" style="vertical-align:middle;padding-right:4px;'
            f'font-size:13px;line-height:1;color:{_FG_META};">💵</td>'
            f'<td valign="middle" style="vertical-align:middle;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'font-weight:600;color:{_FG};line-height:1.3;">{label}</td>'
            f'</tr></table>'
            f'</td>'
            f'<td width="120" align="right" valign="middle" '
            f'style="font-family:Arial,Helvetica,sans-serif;font-size:16px;'
            f'font-weight:700;color:{_GREEN};padding:12px 0;white-space:nowrap;'
            f'font-variant-numeric:tabular-nums;width:120px;text-align:right;{border}">'
            f'{value}</td>'
            f'</tr>'
        )
    metrics = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="padding-top:20px;padding-bottom:4px;">'
        f'{rows_html}</table>'
        if rows_html else ""
    )

    # ── REASON with "Analysis" label ─────────────────────────────────────────
    reason_divider = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:12px 0 16px;">'
        f'<tr><td style="font-size:0;line-height:1px;'
        f'border-top:1px solid {_ROW_SEP};"></td></tr></table>'
    )
    reason_block = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:10px;'
        f'font-weight:700;color:{_FG_META};letter-spacing:0.8px;'
        f'text-transform:uppercase;margin:0 0 6px;">Analysis</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:400;color:{_FG_DIM};margin:0;line-height:20px;">'
        f'{reason}</p>'
    )

    # Left accent border if type has one
    accent_style = f"border-left:4px solid {accent_color};" if accent_color else ""

    card_html = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{_CARD_BG}" style="background-color:{_CARD_BG};'
        f'border-radius:18px;border:1px solid {_DIVIDER};{accent_style}margin-bottom:16px;">'
        f'<tr><td style="padding:24px;">'
        f'{header}{divider}{metrics}{extra_html}{reason_divider}{reason_block}'
        f'</td></tr></table>'
    )

    if is_top:
        top_label = (
            f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:10px;'
            f'font-weight:700;color:{accent_color or _FG_META};letter-spacing:1px;'
            f'text-transform:uppercase;margin:0 0 6px;">★ Top Recommendation</p>'
        )
        html = top_label + card_html
    else:
        html = card_html

    return html, attachments


# ── Section builders ───────────────────────────────────────────────────────────
# Each returns (html, preheader, attachments).

def _section_header(title: str, subtitle: str) -> str:
    return (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:18px;'
        f'font-weight:700;color:{_FG};margin:0 0 6px;">{title}</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'font-weight:400;color:{_FG_DIM};margin:0 0 16px;line-height:20px;">'
        f'{subtitle}</p>'
    )


def _section_wrap(content: str) -> str:
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="padding-top:20px;margin-top:20px;border-top:1px solid {_DIVIDER};">'
        f'<tr><td>{content}</td></tr></table>'
    )


def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str, Attachments]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider pausing: {names} before your next billing date."
    bg, fg    = _BADGE_COLORS["suspend"]
    accent    = _ACCENT_COLORS["suspend"]
    cards_html, attachments = [], {}

    for i, rec in enumerate(recommendations):
        monthly = float(rec.get("estimated_monthly_spend") or rec.get("monthly_cost") or 0.0)
        annual  = float(rec.get("estimated_annual_spend")  or rec.get("annual_cost")  or 0.0)
        if monthly and not annual:
            annual = round(monthly * 12, 2)
        elif annual and not monthly:
            monthly = round(annual / 12, 2)

        card_html, card_atts = _build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Pause",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Next billing: {rec['billing_date']}",
            cost_rows    = [
                ("Monthly savings", f"${monthly:.2f}/mo"),
                ("Annual savings",  f"${annual:.2f}/yr"),
            ],
            reason       = rec.get("reason", "No recent activity or upcoming releases detected before renewal."),
            accent_color = accent,
            confidence   = rec.get("confidence", ""),
            is_top       = (i == 0),
        )
        cards_html.append(card_html)
        attachments.update(card_atts)

    content = _section_header("Services to Pause", "Services with low recent engagement.") + "".join(cards_html)
    return content, preheader, attachments


def _build_consider_non_renewal_content(recommendations: list[dict]) -> tuple[str, str, Attachments]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — low engagement detected."
    bg, fg    = _BADGE_COLORS["consider_non_renewal"]
    accent    = _ACCENT_COLORS["consider_non_renewal"]
    cards_html, attachments = [], {}

    for i, rec in enumerate(recommendations):
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        extra = (
            f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;'
            f'color:{_ORANGE};margin:8px 0 0;line-height:18px;">'
            f'Your plan is paid through {rec["billing_date"]}. '
            f'You can disable auto-renew without losing access.</p>'
        )
        card_html, card_atts = _build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Consider Non-Renewal",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost", f"${annual:.2f}/yr")],
            reason       = rec.get("reason", "Low engagement near renewal window."),
            extra_html   = extra,
            accent_color = accent,
            confidence   = rec.get("confidence", ""),
            is_top       = (i == 0),
        )
        cards_html.append(card_html)
        attachments.update(card_atts)

    content = (
        _section_header("Annual Renewals to Review", "Low engagement near renewal — consider disabling auto-renew.")
        + "".join(cards_html)
    )
    return content, preheader, attachments


def _build_renew_soon_content(recommendations: list[dict]) -> tuple[str, str, Attachments]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — looks worth keeping."
    bg, fg    = _BADGE_COLORS["renew_soon"]
    accent    = _ACCENT_COLORS["renew_soon"]
    cards_html, attachments = [], {}

    for i, rec in enumerate(recommendations):
        annual     = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until = rec.get("days_until_renewal", "?")
        card_html, card_atts = _build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Likely Worth Renewing",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Annual renewal in {days_until} days ({rec['billing_date']})",
            cost_rows    = [("Annual cost", f"${annual:.2f}/yr")],
            reason       = rec.get("reason", "Active engagement and upcoming content detected."),
            accent_color = accent,
            confidence   = rec.get("confidence", ""),
            is_top       = (i == 0),
        )
        cards_html.append(card_html)
        attachments.update(card_atts)

    content = (
        _section_header("Annual Renewals Approaching", "Active engagement detected — these look worth keeping.")
        + "".join(cards_html)
    )
    return content, preheader, attachments


def _build_resubscribe_content(recommendations: list[dict]) -> tuple[str, str, Attachments]:
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Shows returning on {names} — time to resubscribe!"
    bg, fg    = _BADGE_COLORS["resubscribe"]
    accent    = _ACCENT_COLORS["resubscribe"]
    cards_html, attachments = [], {}

    for i, rec in enumerate(recommendations):
        shows = rec.get("shows", [])
        show_rows = "".join(
            f'<tr>'
            f'<td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'color:{_FG};padding:8px 0;border-top:1px solid {_ROW_SEP};">'
            f'{s["title"]}</td>'
            f'<td align="right" width="120" '
            f'style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'color:{_ORANGE};padding:8px 0;white-space:nowrap;'
            f'border-top:1px solid {_ROW_SEP};">'
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

        card_html, card_atts = _build_card(
            name         = rec["name"],
            icon_path    = rec.get("icon_path"),
            badge_label  = "Resubscribe",
            badge_bg     = bg,
            badge_fg     = fg,
            billing_line = f"Last billing date on file: {rec['billing_date']}",
            cost_rows    = [],
            reason       = "High-interest content is returning to this service.",
            extra_html   = extra,
            accent_color = accent,
            confidence   = rec.get("confidence", ""),
            is_top       = (i == 0),
        )
        cards_html.append(card_html)
        attachments.update(card_atts)

    content = (
        _section_header("Services to Resubscribe", "Previously paused services with returning content.")
        + "".join(cards_html)
    )
    return content, preheader, attachments


# ── Public API ─────────────────────────────────────────────────────────────────

def build_report(
    report_type: Literal["suspend", "consider_non_renewal", "renew_soon", "resubscribe"],
    recommendations: list[dict],
) -> tuple[str, Attachments]:
    """Build a single-type HTML email. Returns (html, attachments)."""
    dispatch = {
        "suspend":              (_build_suspend_content,              "Streamsaver — Services to Pause"),
        "consider_non_renewal": (_build_consider_non_renewal_content, "Streamsaver — Annual Renewals to Review"),
        "renew_soon":           (_build_renew_soon_content,           "Streamsaver — Annual Renewals Approaching"),
        "resubscribe":          (_build_resubscribe_content,          "Streamsaver — Services to Resubscribe"),
    }
    builder, title = dispatch.get(report_type, dispatch["resubscribe"])
    content, preheader, attachments = builder(recommendations)
    header_img, header_cid, header_mime = _get_header_logo()
    if header_cid and header_mime:
        attachments[header_cid] = header_mime
    html = _render(
        _load_template(),
        TITLE=title, PREHEADER=preheader, CONTENT=content,
        YEAR=str(date.today().year), HEADER_IMG=header_img,
    )
    return html, attachments


def _build_summary_metrics(analysis_results: dict) -> str:
    """
    3-column stat row: Monthly spend · Potential savings · Services analyzed.
    Rendered before recommendation sections in the digest.
    """
    all_recs = []
    for key in ("suspend", "consider_non_renewal", "renew_soon", "resubscribe", "keep", "uncertain"):
        all_recs.extend(analysis_results.get(key, []))

    total_monthly = sum(
        float(r.get("estimated_monthly_spend") or r.get("monthly_cost") or 0.0)
        for r in all_recs
    )
    savings_recs = (
        analysis_results.get("suspend", [])
        + analysis_results.get("consider_non_renewal", [])
    )
    potential_savings = sum(
        float(r.get("estimated_monthly_spend") or r.get("monthly_cost") or 0.0)
        for r in savings_recs
    )
    services_analyzed = len(all_recs)

    stats = [
        ("Monthly spend",     f"${total_monthly:.2f}"),
        ("Potential savings", f"${potential_savings:.2f}"),
        ("Services analyzed", str(services_analyzed)),
    ]

    cells = "".join(
        f'<td align="center" valign="top" width="33%" style="width:33%;padding:0 8px;">'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:22px;'
        f'font-weight:700;color:{_GREEN};margin:0 0 4px;line-height:1.2;">{value}</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        f'font-weight:500;color:{_FG_META};margin:0;text-transform:uppercase;'
        f'letter-spacing:0.6px;">{label}</p>'
        f'</td>'
        for label, value in stats
    )

    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{_CARD_BG}" style="background-color:{_CARD_BG};'
        f'border-radius:14px;border:1px solid {_DIVIDER};margin-bottom:24px;">'
        f'<tr><td style="padding:20px 16px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr>{cells}</tr>'
        f'</table>'
        f'</td></tr></table>'
    )


def build_digest(analysis_results: dict) -> tuple[str, Attachments] | None:
    """
    Build a digest email covering all actionable recommendation types.
    Returns (html, attachments) or None if nothing to report.
    attachments is deduplicated — each CID appears once regardless of how
    many cards reference the same service logo.
    """
    sections, preview_parts, attachments = [], [], {}

    _SECTIONS = [
        ("suspend",              _build_suspend_content,              "Pause"),
        ("consider_non_renewal", _build_consider_non_renewal_content, "Review renewal"),
        ("renew_soon",           _build_renew_soon_content,           "Renewing soon"),
        ("resubscribe",          _build_resubscribe_content,          "Resubscribe"),
    ]

    for key, builder, prefix in _SECTIONS:
        recs = analysis_results.get(key, [])
        if recs:
            content, _, section_atts = builder(recs)
            sections.append(content if not sections else _section_wrap(content))
            attachments.update(section_atts)
            preview_parts.append(f"{prefix}: {', '.join(r['name'] for r in recs)}")

    uncertain = analysis_results.get("uncertain", [])
    if uncertain:
        bg, fg = _BADGE_COLORS["uncertain"]
        unc_html, unc_atts = [], {}
        for r in uncertain:
            card_html, card_atts = _build_card(
                name         = r["name"],
                icon_path    = r.get("icon_path"),
                badge_label  = "Verify Billing",
                badge_bg     = bg,
                badge_fg     = fg,
                billing_line = "Billing date appears stale",
                cost_rows    = [],
                reason       = r.get("reason", "Please update the billing date in Streamsaver."),
                accent_color = _ACCENT_COLORS["uncertain"],
            )
            unc_html.append(card_html)
            unc_atts.update(card_atts)

        unc_content = (
            _section_header("Billing Dates Need Verification",
                            "These billing dates appear stale — update them in Streamsaver.")
            + "".join(unc_html)
        )
        sections.append(unc_content if not sections else _section_wrap(unc_content))
        attachments.update(unc_atts)
        preview_parts.append(f"Verify billing: {', '.join(r['name'] for r in uncertain)}")

    if not sections:
        return None

    summary_metrics = _build_summary_metrics(analysis_results)
    header_img, header_cid, header_mime = _get_header_logo()
    if header_cid and header_mime:
        attachments[header_cid] = header_mime

    html = _render(
        _load_template(),
        TITLE="Streamsaver — Daily Subscription Digest",
        PREHEADER=" · ".join(preview_parts),
        CONTENT=summary_metrics + "\n".join(sections),
        YEAR=str(date.today().year),
        HEADER_IMG=header_img,
    )
    return html, attachments
