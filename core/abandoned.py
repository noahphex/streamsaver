"""
core/abandoned.py
Streamsaver — Abandoned show detection
Author: Noah Lee

A show is considered "abandoned" if:
  - The user has manually flagged it (abandoned_override = 1), OR
  - Auto-detection is enabled AND the last watch date exceeds the threshold
    set in settings (1 / 3 / 6 / 12 months → 30 / 90 / 180 / 365 days).

Shows with abandoned_override = 1 are never auto-cleared; that flag must be
manually removed by the user in the GUI.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from db import db_session

logger = logging.getLogger(__name__)


def _get_setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def apply_abandoned_detection() -> int:
    """
    Update `is_abandoned` for all shows based on current settings.
    Returns the number of shows whose flag changed.
    """
    with db_session() as conn:
        mode      = _get_setting(conn, "abandoned_mode") or "manual"
        threshold = int(_get_setting(conn, "abandoned_threshold_days") or 90)

        shows = conn.execute(
            "SELECT id, last_watch_date, abandoned_override FROM shows"
        ).fetchall()

        changed = 0
        today   = date.today()

        for show in shows:
            show_id   = show["id"]
            override  = bool(show["abandoned_override"])
            raw_date  = show["last_watch_date"]

            # Manual override is always authoritative
            if override:
                new_flag = 1
            elif mode == "auto" and raw_date:
                try:
                    last = date.fromisoformat(raw_date)
                    new_flag = 1 if (today - last) >= timedelta(days=threshold) else 0
                except ValueError:
                    new_flag = 0
            else:
                # Auto mode but no watch date yet, or manual mode without override
                new_flag = 0

            result = conn.execute(
                "UPDATE shows SET is_abandoned = ? WHERE id = ? AND is_abandoned != ?",
                (new_flag, show_id, new_flag),
            )
            changed += result.rowcount

    logger.info("Abandoned detection: %d show(s) updated.", changed)
    return changed


def set_manual_abandoned(trakt_id: str, abandoned: bool) -> None:
    """
    Set or clear the manual abandoned override for a show.
    When setting to True: also sets is_abandoned = 1.
    When setting to False: clears override and re-evaluates via auto detection.
    """
    with db_session() as conn:
        override_val = 1 if abandoned else 0
        abandoned_val = 1 if abandoned else 0
        conn.execute(
            "UPDATE shows SET abandoned_override = ?, is_abandoned = ? WHERE trakt_id = ?",
            (override_val, abandoned_val, trakt_id),
        )

    if not abandoned:
        # Re-apply auto detection to determine the correct is_abandoned state
        apply_abandoned_detection()
