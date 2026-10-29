"""Spend summaries from the analysis's rolled-forward billing dates."""

from datetime import date, timedelta
from decimal import Decimal


def next_30_days_spend(results, today=None):
    """Count one next bill per subscribed service, including both window edges."""
    today = today or date.today()
    end = today + timedelta(days=30)
    total = Decimal("0")
    count = 0
    seen = set()
    for recs in results.values():
        for rec in recs:
            if rec.get("status") != "subscribed":
                continue
            try:
                billing = date.fromisoformat(rec.get("billing_date") or "")
            except ValueError:
                continue
            identity = rec.get("id", rec.get("name"))
            if today <= billing <= end and identity not in seen:
                cost = "annual_cost" if rec.get("billing_cycle") == "annual" else "monthly_cost"
                total += Decimal(str(rec.get(cost) or 0))
                count += 1
                seen.add(identity)
    return {"total": float(total.quantize(Decimal("0.01"))), "count": count}


def potential_monthly_savings(results):
    return sum(
        (rec.get("annual_cost") or 0) / 12 if rec.get("billing_cycle") == "annual"
        else (rec.get("monthly_cost") or 0)
        for key in ("suspend", "consider_non_renewal") for rec in results.get(key, [])
    )


def spend_headline(totals, timeline_or_rail, savings, subscribed_count, todo=()):
    """Spoken spend copy derived from actual bills, including annual charges."""
    from core.rail import build_rail

    rail = (timeline_or_rail if isinstance(timeline_or_rail, dict)
            else build_rail(timeline_or_rail))
    bills = rail["bills"]
    if not bills:
        parts = [{"text": "Nothing bills in the next 30 days.", "tone": "quiet"}]
    else:
        amount = sum((Decimal(str(bill["amount"])) for bill in bills), Decimal("0"))
        parts = [{"text": f"${amount:,.2f}", "tone": "mono"},
                 {"text": " lands in the next 30 days", "tone": "quiet"}]
        nothing_new = sum(bill["verdict_kind"] == "nothing_new" for bill in bills)
        clause = None
        if nothing_new:
            clause = {"text": f"{nothing_new} {'bill has' if nothing_new == 1 else 'bills have'} nothing coming back first.",
                      "tone": "yellow", "emphasis": True}
        elif all(bill["verdict_kind"] == "returns" for bill in bills):
            clause = {"text": "every bill has a show coming back first.",
                      "tone": "green", "emphasis": True}
        parts.append({"text": ", and " if clause else ".", "tone": "quiet"})
        if clause:
            parts.append(clause)
    if todo:
        parts[-1]["text"] = parts[-1]["text"].rstrip(".")
        parts.extend([{"text": ", and ", "tone": "quiet"},
                      {"text": f"{len(todo)} {'thing' if len(todo) == 1 else 'things'} to change.",
                       "tone": todo[0]["color"], "emphasis": True, "href": "#todo"}])
    return {"lead": "You pay" if subscribed_count else "You're not paying for any streaming right now.",
            "amount": f"${totals['monthly_total']:,.2f}" if subscribed_count else "",
            "tail": f"a month for {subscribed_count} {'service' if subscribed_count == 1 else 'services'}." if subscribed_count else "",
            "sub_parts": parts, "savings": f"Could save ${savings:,.2f}/mo." if savings > 0 else ""}
