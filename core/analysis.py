"""
core/analysis.py
Streamsaver — Suspend / resubscribe recommendation engine
Author: Noah Lee

Logic:
  SUSPEND: A subscribed service has no watchable content and no upcoming
           premieres before its next billing date.

  RESUBSCRIBE: A suspended or unsubscribed service has a show with a premiere
               date that falls before its next billing date.

Returns a structured dict ready for the report builder and email sender.
"""

import logging
from datetime import date
from typing import Optional

from db import db_session

logger = logging.getLogger(__name__)


def _parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _has_upcoming_content(service_id: int, billing_date: date, conn) -> bool:
    """
    Return True if any non-abandoned show on this service has a next episode
    or a premiere arriving on or before the billing date.
    """
    rows = conn.execute(
        """
        SELECT next_episode, next_premiere_date
        FROM   shows
        WHERE  service_id = ?
          AND  is_abandoned = 0
        """,
        (service_id,),
    ).fetchall()

    for row in rows:
        # A watchable next episode exists (no date check needed — it's already out)
        if row["next_episode"]:
            return True
        # A premiere is scheduled before the billing date
        premiere = _parse_date(row["next_premiere_date"])
        if premiere and premiere <= billing_date:
            return True

    # Also check watchlist items
    wl_rows = conn.execute(
        "SELECT premiere_date FROM watchlist WHERE service_id = ?",
        (service_id,),
    ).fetchall()
    for row in wl_rows:
        premiere = _parse_date(row["premiere_date"])
        if premiere and premiere <= billing_date:
            return True

    return False


def _upcoming_premieres_for_service(
    service_id: int, billing_date: date, conn
) -> list[dict]:
    """Return list of show titles + premiere dates arriving before billing_date."""
    premieres = []

    rows = conn.execute(
        """
        SELECT title, next_premiere_date
        FROM   shows
        WHERE  service_id = ?
          AND  is_abandoned = 0
          AND  next_premiere_date IS NOT NULL
        """,
        (service_id,),
    ).fetchall()
    for row in rows:
        d = _parse_date(row["next_premiere_date"])
        if d and d <= billing_date:
            premieres.append({"title": row["title"], "premiere_date": str(d)})

    wl_rows = conn.execute(
        "SELECT title, premiere_date FROM watchlist WHERE service_id = ?",
        (service_id,),
    ).fetchall()
    for row in wl_rows:
        d = _parse_date(row["premiere_date"])
        if d and d <= billing_date:
            premieres.append({"title": row["title"], "premiere_date": str(d)})

    return premieres


def run_analysis() -> dict:
    """
    Analyse all services and return recommendations.

    Returns:
        {
            "suspend":      [ { service, billing_date, savings_monthly, ... }, ... ],
            "resubscribe":  [ { service, billing_date, premieres: [...], ... }, ... ],
            "ok":           [ { service, ... }, ... ],
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

            # We can only reason about services with a known billing date
            if not billing_date:
                logger.debug(
                    "Skipping service '%s' — no billing date set.", svc_dict["name"]
                )
                continue

            if billing_date < today:
                # Billing date has already passed — data needs updating
                logger.debug(
                    "Service '%s' billing date is in the past.", svc_dict["name"]
                )
                continue

            if status == "subscribed":
                has_content = _has_upcoming_content(svc_dict["id"], billing_date, conn)
                if has_content:
                    ok_list.append(_service_summary(svc_dict, billing_date))
                else:
                    suspend_list.append(
                        {
                            **_service_summary(svc_dict, billing_date),
                            "reason": (
                                "No upcoming episodes or premieres before next billing date."
                            ),
                        }
                    )

            elif status in ("suspended", "unsubscribed"):
                premieres = _upcoming_premieres_for_service(
                    svc_dict["id"], billing_date, conn
                )
                if premieres:
                    resubscribe_list.append(
                        {
                            **_service_summary(svc_dict, billing_date),
                            "premieres": premieres,
                        }
                    )

    logger.info(
        "Analysis complete — suspend: %d, resubscribe: %d, ok: %d",
        len(suspend_list), len(resubscribe_list), len(ok_list),
    )
    return {
        "suspend":     suspend_list,
        "resubscribe": resubscribe_list,
        "ok":          ok_list,
    }


def _service_summary(svc: dict, billing_date: date) -> dict:
    """Build a compact summary dict for a service recommendation."""
    return {
        "id":              svc["id"],
        "name":            svc["name"],
        "status":          svc["status"],
        "monthly_cost":    svc["monthly_cost"],
        "annual_cost":     svc["annual_cost"],
        "billing_date":    str(billing_date),
        "icon_path":       svc.get("icon_path"),
    }
