"""Read-only bill and return events from analysis dates and tracked shows."""

from datetime import date, timedelta
from decimal import Decimal

from core.analysis import ANNUAL_RENEWAL_EVAL_WINDOW, service_watch_signals
from core.show_tracker import visible_next_air_date
from db import db_session


def build_timeline(results, today=None, days=60):
    """Return chronological events grouped into Monday-based weeks."""
    today = today or date.today()
    end = today + timedelta(days=days)
    events = []
    seen = set()
    todos = {
        rec.get("id") or rec.get("name"): {"label": label}
        for key, label in (("suspend", "Pause suggested"),
                           ("consider_non_renewal", "Review renewal"))
        for rec in results.get(key, [])
    }
    with db_session() as conn:
        for key in ("keep", "suspend", "consider_non_renewal", "renew_soon", "snoozed"):
            for rec in results.get(key, []):
                if rec.get("status") != "subscribed":
                    continue
                try:
                    billing = date.fromisoformat(rec.get("billing_date") or "")
                except ValueError:
                    continue
                identity = rec.get("id") or rec.get("name")
                if not today <= billing <= end or identity in seen:
                    continue
                seen.add(identity)
                cycle = rec.get("billing_cycle") or "monthly"
                until_bill = (billing - today).days
                if cycle == "annual" and until_bill > ANNUAL_RENEWAL_EVAL_WINDOW:
                    verdict = {"kind": "prepaid", "text": "Annual renewal"}
                else:
                    signals = service_watch_signals(rec.get("id"), conn, today)
                    titles = [a["title"] for a in signals["anticipated"]
                              if a["days_away"] is not None and a["days_away"] <= until_bill]
                    verdict = (
                        {"kind": "returns", "shows": titles,
                         "text": f"{len(titles)} {'show returns' if len(titles) == 1 else 'shows return'} before this bill"}
                        if titles else
                        {"kind": "nothing_new", "text": "Nothing new before this bill"}
                    )
                event = dict(type="bill", date=billing.isoformat(), service_name=rec["name"],
                             service_id=rec.get("id"), icon_path=rec.get("icon_path"),
                             amount=rec.get("annual_cost" if cycle == "annual" else "monthly_cost") or 0,
                             cycle=cycle, verdict=verdict)
                if identity in todos:
                    event["todo"] = todos[identity]
                events.append(event)

        rows = conn.execute("""
            SELECT s.title, s.show_state, s.next_air_date, s.last_watch_date,
                   s.primary_provider, svc.name AS service_name, svc.icon_path
            FROM shows s LEFT JOIN services svc ON s.service_id = svc.id
            WHERE s.show_state NOT IN ('ignored', 'archived', 'abandoned')
              AND s.abandoned_override = 0
        """).fetchall()
        for row in rows:
            air = visible_next_air_date(row["next_air_date"], row["last_watch_date"], today)
            if not air:
                continue
            air_date = date.fromisoformat(air[:10])
            if today <= air_date <= end:
                events.append(dict(type="return", date=air_date.isoformat(), title=row["title"],
                                   show_state=row["show_state"],
                                   service_name=row["service_name"] or row["primary_provider"] or "Unknown service",
                                   icon_path=row["icon_path"]))

    events.sort(key=lambda e: (e["date"], e["type"] != "bill",
                               (e.get("title") or e["service_name"]).casefold()))
    this_week = today - timedelta(days=today.weekday())
    weeks = {}
    for event in events:
        day = date.fromisoformat(event["date"])
        week = day - timedelta(days=day.weekday())
        if week not in weeks:
            label = ("This week" if week == this_week else "Next week"
                     if week == this_week + timedelta(days=7)
                     else f"Week of {week:%b} {week.day}")
            weeks[week] = {"label": label, "events": []}
        weeks[week]["events"].append(event)
    return list(weeks.values())


def preview(timeline, days=14):
    """Flatten events in the next days, including both window edges."""
    today = date.today()
    end = today + timedelta(days=days)
    return [event for week in timeline for event in week["events"]
            if today <= date.fromisoformat(event["date"]) <= end]


def timeline_summary(timeline):
    """Count bills and returns, summing actual charges rather than monthly equivalents."""
    events = [event for week in timeline for event in week["events"]]
    bills = [event for event in events if event["type"] == "bill"]
    amount = sum((Decimal(str(event["amount"])) for event in bills), Decimal("0"))
    return {"bills": len(bills), "amount": float(amount.quantize(Decimal("0.01"))),
            "returns": len(events) - len(bills)}
