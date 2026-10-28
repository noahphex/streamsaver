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
