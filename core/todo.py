"""Resolve and snooze service recommendations."""

from datetime import date, timedelta

from core.service_manager import get_service, set_service_status, update_service
from db import db_session

ACTIONABLE_KEYS = (
    "suspend", "consider_non_renewal", "renew_soon", "resubscribe", "uncertain", "untracked",
)
DONE_LABELS = {
    "suspend": "Paused it",
    "resubscribe": "Resubscribed",
    "consider_non_renewal": "Turned off renewal",
}


def snooze(name, action, until):
    with db_session() as conn:
        conn.execute(
            "INSERT INTO todo_snoozes (service_name, action, until) VALUES (?, ?, ?) "
            "ON CONFLICT(service_name, action) DO UPDATE SET until = excluded.until",
            (name, action, until),
        )


def unsnooze(name, action):
    with db_session() as conn:
        conn.execute("DELETE FROM todo_snoozes WHERE service_name = ? AND action = ?", (name, action))


def default_until(rec, today=None):
    today = today or date.today()
    billing = rec.get("billing_date")
    if billing and date.fromisoformat(billing) > today:
        return billing
    return (today + timedelta(days=30)).isoformat()


def apply_snoozes(results, today):
    with db_session() as conn:
        conn.execute("DELETE FROM todo_snoozes WHERE until < ?", (today.isoformat(),))
        rows = conn.execute("SELECT * FROM todo_snoozes").fetchall()
    snoozes = {(r["service_name"], r["action"]): r["until"] for r in rows}
    results.setdefault("snoozed", [])
    for action in ACTIONABLE_KEYS:
        remaining = []
        for rec in results.get(action, []):
            until = snoozes.get((rec["name"], action))
            if until:
                results["snoozed"].append({**rec, "action": action, "until": until})
            else:
                remaining.append(rec)
        results[action] = remaining


def mark_done(service_id, action, rec=None, today=None):
    if action not in DONE_LABELS:
        raise ValueError("Invalid to-do action.")
    service = get_service(service_id)
    if service is None:
        raise LookupError("Service not found.")
    prev = {key: service[key] for key in ("status", "billing_anchor_date", "auto_renew")}
    today = today or date.today()
    if action == "suspend":
        set_service_status(service_id, "suspended")
    elif action == "resubscribe":
        update_service(service_id, status="subscribed", billing_anchor_date=today.isoformat())
    else:
        update_service(service_id, auto_renew=0)
        snooze(service["name"], action, default_until(rec or {}, today))
    return service, prev


def undo_done(service_id, action, prev, name=None):
    if action not in DONE_LABELS:
        raise ValueError("Invalid to-do action.")
    service = get_service(service_id)
    if service is None:
        raise LookupError("Service not found.")
    with db_session() as conn:
        conn.execute(
            "UPDATE services SET status = ?, billing_anchor_date = ?, auto_renew = ? WHERE id = ?",
            (prev["status"], prev["billing_anchor_date"], prev["auto_renew"], service_id),
        )
    if action == "consider_non_renewal":
        unsnooze(name or service["name"], action)
