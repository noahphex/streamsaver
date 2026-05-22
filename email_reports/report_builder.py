"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Builds Dracula-themed HTML emails for suspend and resubscribe recommendations.
"""

import os
from datetime import date
from typing import Literal

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "templates", "base.html"
)


def _load_template() -> str:
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs) -> str:
    """Replace {{KEY}} placeholders in the template."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _service_icon_tag(icon_path: str | None, name: str) -> str:
    """Return an <img> tag if icon_path exists, otherwise an emoji fallback."""
    if icon_path and os.path.exists(icon_path):
        # Inline the image as base64 for portability in email clients
        import base64
        with open(icon_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(icon_path)[1].lstrip(".")
        mime = "image/png" if ext in ("png",) else f"image/{ext}"
        return (
            f'<img src="data:{mime};base64,{data}" '
            f'alt="{name}" width="32" height="32" '
            f'style="vertical-align:middle;margin-right:8px;border-radius:4px;">'
        )
    return "📺 "


# ── Suspend report ─────────────────────────────────────────────────────────────

def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str]:
    """Return (html_content, preheader) for a suspend report."""
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider suspending: {names} before your next billing date."

    cards = []
    for rec in recommendations:
        icon = _service_icon_tag(rec.get("icon_path"), rec["name"])

        # Use stored costs; derive the missing value if only one is set.
        savings_monthly = float(rec.get("monthly_cost") or 0.0)
        savings_annual  = float(rec.get("annual_cost")  or 0.0)
        if savings_monthly and not savings_annual:
            savings_annual = round(savings_monthly * 12, 2)
        elif savings_annual and not savings_monthly:
            savings_monthly = round(savings_annual / 12, 2)

        cards.append(f"""
        <div class="service-card">
          <p class="service-name">{icon}{rec['name']} <span class="badge badge-suspend">Suspend</span></p>
          <p class="service-meta">Next billing: {rec['billing_date']}</p>
          <div class="cost-row">
            <span>Monthly savings if suspended:</span>
            <span class="cost-value">${savings_monthly:.2f}</span>
          </div>
          <div class="cost-row">
            <span>Annual savings:</span>
            <span class="cost-value">${savings_annual:.2f}</span>
          </div>
          <p style="color:#6272a4;font-size:13px;margin:10px 0 0;">
            {rec.get('reason', 'No upcoming content before next billing date.')}
          </p>
        </div>
        """)

    content = f"""
    <h2>🔴 Services to Suspend</h2>
    <p>The following subscribed service(s) have no upcoming episodes or
    premieres before the next billing date. Consider suspending to save money.</p>
    {''.join(cards)}
    """
    return content, preheader


# ── Resubscribe report ─────────────────────────────────────────────────────────

def _build_resubscribe_content(recommendations: list[dict]) -> tuple[str, str]:
    """Return (html_content, preheader) for a resubscribe report."""
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Shows returning on: {names} — time to resubscribe!"

    cards = []
    for rec in recommendations:
        icon = _service_icon_tag(rec.get("icon_path"), rec["name"])
        premieres = rec.get("premieres", [])

        show_items = "".join(
            f"""<li>
              {p['title']}
              <span class="premiere-date">{p['premiere_date']}</span>
            </li>"""
            for p in premieres
        )

        cards.append(f"""
        <div class="service-card">
          <p class="service-name">{icon}{rec['name']}
            <span class="badge badge-resubscribe">Resubscribe</span>
            <span class="badge badge-suspended" style="margin-left:4px;">{rec['status'].title()}</span>
          </p>
          <p class="service-meta">Billing date on file: {rec['billing_date']}</p>
          <h3>Upcoming shows on this service:</h3>
          <ul class="show-list">
            {show_items}
          </ul>
        </div>
        """)

    content = f"""
    <h2>🟢 Services to Resubscribe</h2>
    <p>The following suspended or unsubscribed service(s) have shows returning
    before the next billing date. Consider resubscribing.</p>
    {''.join(cards)}
    """
    return content, preheader


# ── Public API ─────────────────────────────────────────────────────────────────

def build_report(
    report_type: Literal["suspend", "resubscribe"],
    recommendations: list[dict],
) -> str:
    """
    Build and return a complete HTML email string.

    Args:
        report_type:     "suspend" or "resubscribe"
        recommendations: List of service recommendation dicts from analysis.py

    Returns:
        Full HTML string ready to send.
    """
    template = _load_template()

    if report_type == "suspend":
        content, preheader = _build_suspend_content(recommendations)
        title = "Streamsaver — Services to Suspend"
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
