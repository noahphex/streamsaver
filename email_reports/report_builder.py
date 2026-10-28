"""
email_reports/report_builder.py
Streamsaver — HTML email report builder
Author: Noah Lee

Builds Dracula-themed HTML emails for all recommendation types:
  suspend, consider_non_renewal, renew_soon, resubscribe
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


def _build_suspend_content(recommendations: list[dict]) -> tuple[str, str]:
    """Return (html_content, preheader) for a suspend report."""
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Consider suspending: {names} before your next billing date."

    cards = []
    for rec in recommendations:
        icon            = _service_icon_tag(rec.get("icon_path"), rec["name"])
        monthly_savings = rec.get("estimated_monthly_spend") or rec.get("monthly_cost") or 0.0
        annual_savings  = rec.get("estimated_annual_spend")  or rec.get("annual_cost")  or 0.0
        if monthly_savings and not annual_savings:
            annual_savings = round(float(monthly_savings) * 12, 2)
        elif annual_savings and not monthly_savings:
            monthly_savings = round(float(annual_savings) / 12, 2)

        cards.append(f"""
        <div class="service-card">
          <p class="service-name">{icon}{rec['name']} <span class="badge badge-suspend">Suspend</span></p>
          <p class="service-meta">Next billing: {rec['billing_date']}</p>
          <div class="cost-row">
            <span>Monthly savings if suspended:</span>
            <span class="cost-value">${float(monthly_savings):.2f}</span>
          </div>
          <div class="cost-row">
            <span>Annual savings:</span>
            <span class="cost-value">${float(annual_savings):.2f}</span>
          </div>
          <p style="color:#6272a4;font-size:13px;margin:10px 0 0;">
            {rec.get('reason', 'No upcoming content before next billing date.')}
          </p>
        </div>
        """)

    content = f"""
    <h2>🔴 Services to Suspend</h2>
    <p>The following monthly subscriptions have low engagement and no upcoming content
    before the next billing date. Consider suspending to save money.</p>
    {''.join(cards)}
    """
    return content, preheader


# ── Consider non-renewal report ────────────────────────────────────────────────

def _build_consider_non_renewal_content(recommendations: list[dict]) -> tuple[str, str]:
    """Return (html_content, preheader) for a consider-non-renewal report."""
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — low engagement detected."

    cards = []
    for rec in recommendations:
        icon         = _service_icon_tag(rec.get("icon_path"), rec["name"])
        annual_cost  = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until   = rec.get("days_until_renewal", "?")

        cards.append(f"""
        <div class="service-card">
          <p class="service-name">{icon}{rec['name']}
            <span class="badge badge-suspend">Consider Non-Renewal</span>
          </p>
          <p class="service-meta">Annual renewal in {days_until} days ({rec['billing_date']})</p>
          <div class="cost-row">
            <span>Annual cost:</span>
            <span class="cost-value">${annual_cost:.2f}</span>
          </div>
          <p style="color:#6272a4;font-size:13px;margin:10px 0 0;">
            {rec.get('reason', 'Low engagement near renewal window.')}
          </p>
          <p style="color:#ffb86c;font-size:12px;margin:6px 0 0;">
            💡 Your plan is already paid through {rec['billing_date']}.
            You can disable auto-renew now without losing access.
          </p>
        </div>
        """)

    content = f"""
    <h2>🟡 Annual Subscriptions Nearing Renewal</h2>
    <p>The following annual plans are approaching renewal with low recent engagement.
    Your access continues through the renewal date — consider disabling auto-renew
    if you no longer find value.</p>
    {''.join(cards)}
    """
    return content, preheader


# ── Renew soon report ──────────────────────────────────────────────────────────

def _build_renew_soon_content(recommendations: list[dict]) -> tuple[str, str]:
    """Return (html_content, preheader) for a renew-soon report."""
    names     = ", ".join(r["name"] for r in recommendations)
    preheader = f"Annual renewal approaching for {names} — looks worth keeping."

    cards = []
    for rec in recommendations:
        icon       = _service_icon_tag(rec.get("icon_path"), rec["name"])
        annual_cost = float(rec.get("estimated_annual_spend") or rec.get("annual_cost") or 0.0)
        days_until  = rec.get("days_until_renewal", "?")

        cards.append(f"""
        <div class="service-card">
          <p class="service-name">{icon}{rec['name']}
            <span class="badge badge-resubscribe">Likely Worth Renewing</span>
          </p>
          <p class="service-meta">Annual renewal in {days_until} days ({rec['billing_date']})</p>
          <div class="cost-row">
            <span>Annual cost:</span>
            <span class="cost-value">${annual_cost:.2f}</span>
          </div>
          <p style="color:#6272a4;font-size:13px;margin:10px 0 0;">
            {rec.get('reason', 'Active engagement and upcoming content detected.')}
          </p>
        </div>
        """)

    content = f"""
    <h2>🟢 Annual Subscriptions Approaching Renewal</h2>
    <p>The following annual plans are nearing renewal and show strong engagement signals.
    These subscriptions are likely worth keeping.</p>
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

        show_items = "".join(
            f"""<li>
              {s['title']}
              <span class="premiere-date">{s.get('next_air_date', '—')}</span>
            </li>"""
            for s in rec.get("shows", [])
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
    report_type: Literal["suspend", "consider_non_renewal", "renew_soon", "resubscribe"],
    recommendations: list[dict],
) -> str:
    """
    Build and return a complete HTML email string.

    Args:
        report_type:     One of suspend | consider_non_renewal | renew_soon | resubscribe
        recommendations: List of service recommendation dicts from analysis.py

    Returns:
        Full HTML string ready to send.
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
