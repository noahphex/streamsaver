"""
core/newsletter.py
Streamsaver newsletter frequency controls.
"""

from __future__ import annotations

from datetime import date

from db import db_session

STATUS_CHANGE_KEYS = (
    "suspend",
    "consider_non_renewal",
    "renew_soon",
    "resubscribe",
)

FREQUENCY_OPTIONS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}

DEFAULT_FREQUENCY = "weekly"


def get_status_alert_frequency() -> str:
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'status_alert_frequency'"
        ).fetchone()
    value = row["value"] if row else ""
    return value if value in FREQUENCY_OPTIONS or value == "never" else DEFAULT_FREQUENCY


def status_alerts_due(today: date | None = None) -> bool:
    frequency = get_status_alert_frequency()
    if frequency == "never":
        return False

    today = today or date.today()
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'last_status_alert_sent'"
        ).fetchone()
    last_sent = _parse_date(row["value"] if row else "")
    if last_sent is None:
        return True
    return (today - last_sent).days >= FREQUENCY_OPTIONS[frequency]


def filter_status_alerts_for_frequency(results: dict, today: date | None = None) -> tuple[dict, bool]:
    """
    Remove status-change newsletter sections when the configured interval has not elapsed.

    Returns (filtered_results, included_status_alerts).
    """
    if status_alerts_due(today):
        return results, True

    filtered = dict(results)
    for key in STATUS_CHANGE_KEYS:
        filtered[key] = []
    return filtered, False


def mark_status_alerts_sent(today: date | None = None) -> None:
    today = today or date.today()
    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_status_alert_sent', ?)",
            (today.isoformat(),),
        )


def has_status_alerts(results: dict) -> bool:
    return any(results.get(key) for key in STATUS_CHANGE_KEYS)


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None
