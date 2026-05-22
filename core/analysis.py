"""
core/analysis.py
Streamsaver — Suspend / resubscribe recommendation engine
Author: Noah Lee

Logic:
  SUSPEND:     A subscribed service has no active/occasional shows and no
               anticipated shows with a premiere before the next billing date.

  RESUBSCRIBE: A suspended/unsubscribed service has an anticipated show with
               a premiere before the billing date AND the user previously
               engaged with the show (interest_score > 10).

  OK:          The service has active content — no action needed.

Show states considered:
  active / occasional  → user is watching, service has value
  anticipated          → upcoming season, resubscribe candidate
  dormant / archived   → low signal, not counted
"""

import logging
from datetime import date
from typing import Optional

from db import db_session

logger = logging.getLogger(__name__)

# Minimum interest score to trigger a resubscribe recommendation
_RESUBSCRIBE_SCORE_THRESHOLD = 10


def _parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _service_watch_score(service_id: int, conn) -> dict:
    """
    Aggregate watch signals for a service.

    Returns:
        active_count  — number of Active/Occasional shows
        watch_score   — sum of interest_scores for Active/Occasional shows
        anticipated   — list of anticipated shows with next_air_date
    """
    rows = conn.execute(
        """
        SELECT show_state, interest_score, title, next_air_date
        FROM   shows
        WHERE  service_id = ?
          AND  show_state NOT IN ('ignored', 'archived')
        """,
        (service_id,),
    ).fetchall()

    active_count = 0
    watch_score  = 0
    anticipated  = []

    for row in rows:
        state = row["show_state"]
        if state in ("active", "occasional"):
            active_count += 1
            watch_score  += row["interest_score"]
        elif state == "anticipated":
            anticipated.append({
                "title":        row["title"],
                "next_air_date": row["next_air_date"],
                "score":        row["interest_score"],
            })

    return {
        "active_count": active_count,
        "watch_score":  watch_score,
        "anticipated":  anticipated,
    }


def _watchlist_premieres_before(service_id: int, billing_date: date, conn) -> list[dict]:
    """Return watchlist items for this service with premiere ≤ billing_date."""
    rows = conn.execute(
        "SELECT title, premiere_date FROM watchlist WHERE service_id = ?",
        (service_id,),
    ).fetchall()
    result = []
    for row in rows:
        d = _parse_date(row["premiere_date"])
        if d and d <= billing_date:
            result.append({"title": row["title"], "premiere_date": str(d)})
    return result


def run_analysis() -> dict:
    """
    Analyse all services and return suspend/resubscribe/ok recommendations.

    Returns:
        {
            "suspend":     [{ service, billing_date, watch_score, ... }],
            "resubscribe": [{ service, billing_date, shows: [...], ... }],
            "ok":          [{ service, active_count, watch_score, ... }],
        }
    """
    suspend_list:     list[dict] = []
    resubscribe_list: list[dict] = []
    ok_list:          list[dict] = []

    today = date.today()

    with db_session() as conn:
        services = conn.execute("SELECT * FROM services").fetchall()

        for svc in services:
            svc_dict     = dict(svc)
            billing_date = _parse_date(svc_dict.get("next_billing_date"))
            status       = svc_dict["status"]

            if not billing_date:
                logger.debug("Skipping '%s' — no billing date.", svc_dict["name"])
                continue
            if billing_date < today:
                logger.debug("Skipping '%s' — billing date in the past.", svc_dict["name"])
                continue

            signals = _service_watch_score(svc_dict["id"], conn)
            summary = _service_summary(svc_dict, billing_date, signals)

            if status == "subscribed":
                if signals["active_count"] > 0:
                    # Has shows being actively watched — keep subscription
                    ok_list.append(summary)
                else:
                    # Check for anticipated shows before billing date
                    upcoming = [
                        s for s in signals["anticipated"]
                        if _parse_date(s["next_air_date"]) and
                           _parse_date(s["next_air_date"]) <= billing_date
                    ]
                    # Check watchlist items
                    wl = _watchlist_premieres_before(svc_dict["id"], billing_date, conn)

                    if upcoming or wl:
                        ok_list.append({**summary, "upcoming": upcoming, "watchlist": wl})
                    else:
                        suspend_list.append({
                            **summary,
                            "reason": (
                                f"No active shows and no upcoming premieres before {billing_date}. "
                                f"Watch score: {signals['watch_score']}."
                            ),
                        })

            elif status in ("suspended", "unsubscribed"):
                # Look for anticipated shows with enough engagement to warrant resubscribing
                resubscribe_shows = [
                    s for s in signals["anticipated"]
                    if s["score"] > _RESUBSCRIBE_SCORE_THRESHOLD
                    and _parse_date(s["next_air_date"])
                    and _parse_date(s["next_air_date"]) <= billing_date
                ]
                if resubscribe_shows:
                    resubscribe_list.append({
                        **summary,
                        "shows": resubscribe_shows,
                    })

    logger.info(
        "Analysis complete — suspend: %d, resubscribe: %d, ok: %d",
        len(suspend_list), len(resubscribe_list), len(ok_list),
    )
    return {
        "suspend":     suspend_list,
        "resubscribe": resubscribe_list,
        "ok":          ok_list,
    }


def _service_summary(svc: dict, billing_date: date, signals: dict) -> dict:
    """Build a compact summary dict for a service recommendation."""
    return {
        "id":           svc["id"],
        "name":         svc["name"],
        "status":       svc["status"],
        "monthly_cost": svc["monthly_cost"],
        "annual_cost":  svc["annual_cost"],
        "billing_date": str(billing_date),
        "icon_path":    svc.get("icon_path"),
        "active_count": signals["active_count"],
        "watch_score":  signals["watch_score"],
    }
