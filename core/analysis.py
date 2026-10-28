"""
core/analysis.py
Streamsaver — Suspend / resubscribe recommendation engine
Author: Noah Lee

Recommendation types:
  KEEP                 — active annual service, renewal far enough away; no action needed
  SUSPEND              — active monthly service with low engagement
  CONSIDER_NON_RENEWAL — active annual service nearing renewal with low engagement
  RENEW_SOON           — active annual service nearing renewal with high confidence of value
  RESUBSCRIBE          — inactive service with strong prior engagement + upcoming content
  UNCERTAIN            — billing date is stale/missing; user should verify

Billing model:
  Monthly services are evaluated at every billing date (roll the anchor forward
  month-by-month; an old monthly anchor is never stale).
  Annual services are only evaluated within ANNUAL_RENEWAL_EVAL_WINDOW days of renewal
  (roll forward one cycle; beyond ANNUAL_STALE_GRACE_DAYS past renewal → uncertain).
"""

import calendar
import logging
from datetime import date, timedelta
from typing import Optional

from db import db_session
from core.service_manager import estimated_monthly_spend, estimated_annual_spend

logger = logging.getLogger(__name__)

# ── Tunable constants (internal — not user-configurable yet) ───────────────────
ANNUAL_RENEWAL_EVAL_WINDOW   = 60   # days before annual renewal to start evaluating
RENEW_SOON_WINDOW            = 45   # tighter window for positive (renew) signal
ANNUAL_STALE_GRACE_DAYS      = 30   # days past annual renewal before marking uncertain
LOW_USAGE_SCORE              = 5    # watch_score below this = low engagement
RENEW_SOON_ACTIVE_THRESHOLD  = 2    # min active shows for high-confidence renewal
RESUBSCRIBE_SCORE_THRESHOLD  = 15   # min interest_score (scores top out at 28; 15 = "active") for resubscribe
UPCOMING_CONTENT_DAYS        = 30   # days window for "upcoming" content signal


def _parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _activity_label(watch_score: int, active_count: int) -> str:
    """Return human-readable viewer activity label from raw scores."""
    if active_count > 0 or watch_score >= 70:
        return "High"
    if watch_score >= LOW_USAGE_SCORE:
        return "Medium"
    if watch_score > 0:
        return "Low"
    return "None"


def _confidence_label(watch_score: int, active_count: int) -> str:
    """
    Return recommendation confidence based on signal clarity.
    High = very clear signal (strongly active or completely inactive).
    Medium = some signal but mixed.
    Low = minimal data.
    """
    if watch_score == 0 and active_count == 0:
        return "High"     # completely inactive — unambiguous
    if active_count >= 2 or watch_score >= 50:
        return "High"     # strong engagement — unambiguous
    if watch_score >= LOW_USAGE_SCORE or active_count > 0:
        return "Medium"
    return "Low"


def _advance_billing_date(anchor: date, cycle: str, today: date) -> tuple[date, bool]:
    """
    Roll a billing anchor forward to the next future billing date.

    Returns (next_billing_date, is_uncertain) where is_uncertain=True means
    the date drifted past the staleness threshold.
    """
    step = 12 if cycle == "annual" else 1
    cycles = 0
    d = anchor
    while d < today:
        cycles += 1
        d = _add_months(anchor, step * cycles)  # always from the anchor, so the 31st stays the 31st

    # Monthly billing just repeats, so an old anchor is still valid. An annual plan more than
    # one cycle behind, and past the grace period, means the user likely changed plans.
    if cycle == "annual" and cycles > 1:
        previous = _add_months(anchor, step * (cycles - 1))
        if (today - previous).days > ANNUAL_STALE_GRACE_DAYS:
            return d, True
    return d, False


def _add_months(d: date, months: int) -> date:
    """Shift by whole months, clamping the day (Jan 31 + 1 → Feb 28/29, Feb 29 + 12 → Feb 28)."""
    years, month0 = divmod(d.month - 1 + months, 12)
    year, month = d.year + years, month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _service_watch_score(service_id: int, conn) -> dict:
    """
    Aggregate watch signals for a service.

    Returns:
        active_count   — Active/Occasional shows
        watch_score    — sum of interest_scores for Active/Occasional
        anticipated    — list of anticipated shows with next_air_date
        upcoming_days  — min days until next anticipated air date (or None)
    """
    rows = conn.execute(
        """
        SELECT show_state, interest_score, title, next_air_date
        FROM   shows
        WHERE  service_id = ?
          AND  show_state NOT IN ('ignored', 'archived', 'completed')
        """,
        (service_id,),
    ).fetchall()

    active_count = 0
    watch_score  = 0
    anticipated  = []
    today        = date.today()

    for row in rows:
        state = row["show_state"]
        if state in ("active", "occasional"):
            active_count += 1
            watch_score  += row["interest_score"]
        elif state == "anticipated":
            air = _parse_date(row["next_air_date"])
            days_away = (air - today).days if air else None
            if days_away is not None and days_away < 0:
                continue  # already aired: not upcoming content
            anticipated.append({
                "title":        row["title"],
                "next_air_date": row["next_air_date"],
                "score":        row["interest_score"],
                "days_away":    days_away,
            })

    upcoming_days = None
    if anticipated:
        near = [a["days_away"] for a in anticipated if a["days_away"] is not None]
        if near:
            upcoming_days = min(near)

    return {
        "active_count":  active_count,
        "watch_score":   watch_score,
        "anticipated":   anticipated,
        "upcoming_days": upcoming_days,
    }


def _renew_soon_confidence(signals: dict) -> bool:
    """
    Return True only if there is high confidence the service is worth renewing.
    Conservative — requires multiple strong signals.
    """
    return (
        signals["active_count"] >= RENEW_SOON_ACTIVE_THRESHOLD
        or signals["watch_score"] >= 70
        or (
            signals["upcoming_days"] is not None
            and signals["upcoming_days"] <= UPCOMING_CONTENT_DAYS
        )
    )


def run_analysis() -> dict:
    """
    Analyse all services and return layered recommendations.

    Returns:
        {
            "keep":                 [...],
            "suspend":              [...],
            "consider_non_renewal": [...],
            "renew_soon":           [...],
            "resubscribe":          [...],
            "uncertain":            [...],
            "untracked":            [{"name": provider, "shows": [titles]}],
        }
    """
    result: dict[str, list] = {
        "keep":                 [],
        "suspend":              [],
        "consider_non_renewal": [],
        "renew_soon":           [],
        "resubscribe":          [],
        "uncertain":            [],
        "untracked":            [],
    }

    today = date.today()

    with db_session() as conn:
        services = conn.execute("SELECT * FROM services").fetchall()

        for svc in services:
            s            = dict(svc)
            status       = s["status"]
            cycle        = s.get("billing_cycle") or "monthly"
            auto_renew   = s.get("auto_renew", 1)
            anchor_raw   = s.get("billing_anchor_date") or s.get("next_billing_date")
            anchor       = _parse_date(anchor_raw)

            # Paid services need a billing date; an inactive one has no bill, so judge it from today
            # (otherwise unsubscribed services, which rarely have a date, never get resubscribe advice).
            if not anchor and status not in ("suspended", "unsubscribed"):
                logger.debug("Skipping '%s' — no billing anchor date.", s["name"])
                continue

            # Roll billing date forward and check for staleness
            next_billing, is_uncertain = _advance_billing_date(anchor, cycle, today) if anchor else (today, False)
            days_until_renewal = (next_billing - today).days

            signals = _service_watch_score(s["id"], conn)
            summary = _build_summary(s, next_billing, signals, cycle, days_until_renewal)
            if not anchor:
                summary["billing_date"] = ""  # don't present today as a billing date

            # ── Active subscriptions ───────────────────────────────────────
            if status == "subscribed":
                if not (s.get("monthly_cost") or s.get("annual_cost")):
                    # Free (e.g. a card perk): pausing or not renewing saves nothing.
                    result["keep"].append({**summary, "reason": "Free ($0) — nothing to save by pausing or not renewing."})
                    continue
                if is_uncertain:
                    result["uncertain"].append({
                        **summary,
                        "reason": f"Billing date appears stale (last anchor: {anchor_raw}). Please verify.",
                    })
                    continue

                if cycle == "annual":
                    if not auto_renew:
                        # Auto-renew disabled — treat like it's expiring
                        if days_until_renewal <= ANNUAL_RENEWAL_EVAL_WINDOW and signals["active_count"] == 0 and signals["watch_score"] < LOW_USAGE_SCORE:
                            result["consider_non_renewal"].append({**summary, "reason": "Auto-renew is off. Low engagement near expiry."})
                        else:
                            result["keep"].append({**summary, "reason": f"Prepaid through {next_billing}. Auto-renew disabled."})
                    elif days_until_renewal > ANNUAL_RENEWAL_EVAL_WINDOW:
                        # Already paid and renewal is far away — no action
                        result["keep"].append({
                            **summary,
                            "reason": f"Annual plan prepaid through {next_billing} ({days_until_renewal} days). No action needed.",
                        })
                    elif _renew_soon_confidence(signals):
                        result["renew_soon"].append({
                            **summary,
                            "reason": (
                                f"Annual plan renews in {days_until_renewal} days. "
                                f"{signals['active_count']} active show(s). "
                                f"Viewer activity: {_activity_label(signals['watch_score'], signals['active_count'])}."
                            ),
                        })
                    else:
                        result["consider_non_renewal"].append({
                            **summary,
                            "reason": (
                                f"Annual plan renews in {days_until_renewal} days. "
                                f"Viewer activity: {_activity_label(signals['watch_score'], signals['active_count'])}. "
                                "Consider disabling auto-renew."
                            ),
                        })
                else:
                    # Monthly
                    if signals["active_count"] > 0 or signals["watch_score"] >= LOW_USAGE_SCORE:
                        result["keep"].append({
                                **summary,
                                "reason": f"{signals['active_count']} active show(s). Viewer activity: {_activity_label(signals['watch_score'], signals['active_count'])}.",
                            })
                    else:
                        # Check for upcoming content before billing
                        upcoming_before_billing = [
                            a for a in signals["anticipated"]
                            if a["days_away"] is not None and a["days_away"] <= days_until_renewal
                        ]
                        if upcoming_before_billing:
                            result["keep"].append({
                                **summary,
                                "reason": f"Upcoming content before {next_billing}: {', '.join(a['title'] for a in upcoming_before_billing)}.",
                            })
                        else:
                            result["suspend"].append({
                                **summary,
                                "reason": (
                                    f"No active shows or upcoming content before {next_billing}. "
                                    f"Viewer activity: {_activity_label(signals['watch_score'], signals['active_count'])}."
                                ),
                            })

            # ── Inactive subscriptions ─────────────────────────────────────
            elif status in ("suspended", "unsubscribed"):
                resubscribe_shows = [
                    a for a in signals["anticipated"]
                    if a["score"] >= RESUBSCRIBE_SCORE_THRESHOLD
                    and a["days_away"] is not None
                    and a["days_away"] <= UPCOMING_CONTENT_DAYS
                ]
                if resubscribe_shows:
                    result["resubscribe"].append({
                        **summary,
                        "shows": resubscribe_shows,
                    })

        result["untracked"] = _untracked_providers(conn)

    _log_summary(result)
    return result


def _build_summary(svc: dict, billing_date: date, signals: dict, cycle: str, days_until: int) -> dict:
    """Build a compact summary dict for a service recommendation."""
    return {
        "id":            svc["id"],
        "name":          svc["name"],
        "status":        svc["status"],
        "billing_cycle": cycle,
        "billing_date":  str(billing_date),
        "days_until_renewal": days_until,
        "monthly_cost":  svc.get("monthly_cost") or 0.0,
        "annual_cost":   svc.get("annual_cost") or 0.0,
        "estimated_monthly_spend": estimated_monthly_spend(svc),
        "estimated_annual_spend":  estimated_annual_spend(svc),
        "icon_path":     svc.get("icon_path"),
        "active_count":  signals["active_count"],
        "watch_score":   signals["watch_score"],
        "confidence":    _confidence_label(signals["watch_score"], signals["active_count"]),
    }


def _untracked_providers(conn) -> list[dict]:
    """
    Providers you're watching on that match no configured service, with their shows.
    Catches gaps like Apple TV going unnoticed because no service existed for it.
    """
    rows = conn.execute(
        """
        SELECT primary_provider, title FROM shows
        WHERE service_id IS NULL
          AND COALESCE(primary_provider, '') <> ''
          AND show_state IN ('active', 'occasional', 'anticipated')
        ORDER BY primary_provider, interest_score DESC, title
        """
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["primary_provider"], []).append(row["title"])
    return [{"name": provider, "shows": titles} for provider, titles in grouped.items()]


def _log_summary(result: dict) -> None:
    logger.info(
        "Analysis — keep: %d, suspend: %d, consider_non_renewal: %d, "
        "renew_soon: %d, resubscribe: %d, uncertain: %d, untracked: %d",
        len(result["keep"]),
        len(result["suspend"]),
        len(result["consider_non_renewal"]),
        len(result["renew_soon"]),
        len(result["resubscribe"]),
        len(result["uncertain"]),
        len(result["untracked"]),
    )
