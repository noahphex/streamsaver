"""Read-only bill and return events from analysis dates and tracked shows."""

from datetime import date, timedelta
from decimal import Decimal

from core.analysis import ANNUAL_RENEWAL_EVAL_WINDOW, _advance_billing_date, service_watch_signals
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
                if not today <= billing <= end:
                    continue
                cycle = rec.get("billing_cycle") or "monthly"
                stored = conn.execute("""
                    SELECT COALESCE(NULLIF(billing_anchor_date, ''), next_billing_date) AS anchor
                    FROM services WHERE id = ?
                """, (rec.get("id"),)).fetchone()
                try:
                    anchor = date.fromisoformat(stored["anchor"] or "") if stored else billing
                except ValueError:
                    anchor = billing
                previous = None
                signals = service_watch_signals(rec.get("id"), conn, today)
                while billing <= end:
                    if (identity, billing) in seen:
                        break
                    seen.add((identity, billing))
                    until_bill = (billing - today).days
                    if cycle == "annual" and until_bill > ANNUAL_RENEWAL_EVAL_WINDOW:
                        verdict = {"kind": "prepaid", "text": "Annual renewal"}
                    else:
                        if previous is None:
                            shows = [{"title": a["title"], "date": a["next_air_date"]}
                                     for a in signals["anticipated"]
                                     if a["days_away"] is not None and a["days_away"] <= until_bill]
                        else:
                            shows = [{"title": row["title"], "date": row["next_air_date"]}
                                     for row in conn.execute("""
                                         SELECT title, next_air_date, last_watch_date FROM shows
                                         WHERE service_id = ?
                                           AND show_state NOT IN ('ignored', 'archived', 'abandoned')
                                           AND abandoned_override = 0
                                     """, (rec.get("id"),))
                                     if (air := visible_next_air_date(row["next_air_date"], row["last_watch_date"], today))
                                     and previous < date.fromisoformat(air[:10]) <= billing]
                        shows.sort(key=lambda show: (show["date"], show["title"].casefold()))
                        verdict = (
                            {"kind": "returns", "shows": shows,
                             "text": f"{len(shows)} {'show returns' if len(shows) == 1 else 'shows return'} before this bill"}
                            if shows else
                            {"kind": "nothing_new", "text": "Nothing new before this bill"}
                            if previous is None else
                            # Trakt rarely knows premieres beyond the next few weeks, so a later
                            # empty interval is unknown rather than a reason to pause.
                            {"kind": "unknown", "text": "Too early to tell"}
                        )
                    event = dict(type="bill", date=billing.isoformat(), service_name=rec["name"],
                                 service_id=rec.get("id"), icon_path=rec.get("icon_path"),
                                 amount=rec.get("annual_cost" if cycle == "annual" else "monthly_cost") or 0,
                                 cycle=cycle, verdict=verdict)
                    if previous is None and identity in todos:
                        event["todo"] = todos[identity]
                    events.append(event)
                    if cycle == "annual":
                        break
                    previous = billing
                    billing, _ = _advance_billing_date(anchor, cycle, billing + timedelta(days=1))

        rows = conn.execute("""
            SELECT s.title, s.show_state, s.next_air_date, s.last_watch_date,
                   s.primary_provider, s.service_id, svc.status AS service_status,
                   svc.billing_cycle, svc.name AS service_name, svc.icon_path
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
                bills = sorted(e["date"] for e in events
                               if e["type"] == "bill" and e["service_id"] == row["service_id"])
                next_bill = next((d for d in bills if d >= air_date.isoformat()), None)
                status = row["service_status"]
                if status in ("unsubscribed", "not_subscribed", "suspended"):
                    context = "not subscribed"
                elif next_bill:
                    bill_day = date.fromisoformat(next_bill)
                    context = f"counts toward the {bill_day:%b} {bill_day.day} bill"
                elif status == "subscribed" and row["billing_cycle"] == "annual" and not bills:
                    context = "annual · prepaid"
                elif status == "subscribed" and row["billing_cycle"] != "annual" and bills:
                    context = "after your last bill in this window"
                else:
                    context = ""
                events.append(dict(type="return", date=air_date.isoformat(), title=row["title"],
                                   show_state=row["show_state"], service_id=row["service_id"],
                                   service_status=status, context=context,
                                   resubscribe_todo=any(r.get("id") == row["service_id"]
                                                       for r in results.get("resubscribe", [])),
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


def timeline_summary(timeline):
    """Count bills and returns, summing actual charges rather than monthly equivalents."""
    events = [event for week in timeline for event in week["events"]]
    bills = [event for event in events if event["type"] == "bill"]
    amount = sum((Decimal(str(event["amount"])) for event in bills), Decimal("0"))
    return {"bills": len(bills), "amount": float(amount.quantize(Decimal("0.01"))),
            "returns": len(events) - len(bills)}


def group_periods(timeline, today=None):
    """Group existing verdicts into service billing windows without changing advice."""
    today = today or date.today()
    events = sorted((event for week in timeline for event in week["events"]),
                    key=lambda event: (event["date"], event.get("title", "")))
    bills = [event for event in events if event["type"] == "bill"]
    returns = [event for event in events if event["type"] == "return"]
    periods, later, previous, assigned = [], [], {}, set()
    outside = {"annual · prepaid", "not subscribed", "after your last bill in this window"}
    for bill in bills:
        identity = bill.get("service_id") or bill["service_name"]
        start = previous.get(identity, today.isoformat())
        end = bill["date"]
        previous[identity] = end
        if bill["verdict"]["kind"] in ("unknown", "prepaid"):
            later.append(bill)
            continue
        window = []
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        for index, event in enumerate(returns):
            if (event.get("service_id") is not None and event["service_id"] == bill.get("service_id")
                    and event.get("context") not in outside and start < event["date"] <= end):
                pos = (date.fromisoformat(event["date"]) - date.fromisoformat(start)).days / max(1, span) * 100
                window.append(dict(event, pos=round(max(0, min(100, pos)), 2)))
                assigned.add(index)
        periods.append(dict(bill=bill, window_start=start, window_end=end, returns=window,
                            fill_pct=max((event["pos"] for event in window), default=0),
                            anchor_id=f"bill-{bill.get('service_id')}-{end}"))
    elsewhere = [event for index, event in enumerate(returns) if index not in assigned]
    off_returns = {}
    for event in elsewhere:
        if event.get("service_status") in ("unsubscribed", "not_subscribed", "suspended"):
            off_returns.setdefault(event["service_id"], event)

    def total(items):
        return float(sum((Decimal(str(item["amount"])) for item in items), Decimal("0")).quantize(Decimal("0.01")))

    worth = [period["bill"] for period in periods if period["bill"]["verdict"]["kind"] == "returns"]
    return dict(periods=periods, later=dict(count=len(later), dates=[bill["date"] for bill in later], total=total(later),
                                         anchors=[f"bill-{bill.get('service_id')}-{bill['date']}" for bill in later]),
                elsewhere=elsewhere, off_returns=off_returns,
                summary=dict(worth_paying=len(worth), total=total(worth),
                             by=periods[-1]["window_end"] if periods else None,
                             nothing_new=len(periods) - len(worth), bills=len(bills),
                             returns=len(returns), elsewhere=len(elsewhere)))
