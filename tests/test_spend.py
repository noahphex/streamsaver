from datetime import date, timedelta

from core import spend


def test_next_30_days_includes_both_cycles_edges_and_snoozes():
    today = date(2026, 10, 28)
    def rec(id, offset, **values):
        return dict(id=id, status="subscribed", billing_cycle="monthly", monthly_cost=10,
                    billing_date=str(today + timedelta(days=offset)), **values)
    results = {"keep": [rec(1, 0), rec(2, 30), rec(3, -1), rec(4, 31)],
               "snoozed": [dict(rec(5, 7), billing_cycle="annual", annual_cost=120)],
               "resubscribe": [dict(rec(6, 1), status="suspended")],
               "uncertain": [dict(rec(7, 0), billing_date=""), dict(rec(8, 0), billing_date="invalid")]}
    assert spend.next_30_days_spend(results, today) == {"total": 140, "count": 3}


def test_spend_uses_annual_price_only_on_renewal_and_rounds_money():
    today = date(2026, 10, 28)
    results = {"keep": [dict(id=1, status="subscribed", billing_cycle="annual", annual_cost=99.99,
                             monthly_cost=999, billing_date="2026-11-28")]}
    assert spend.next_30_days_spend(results, today) == {"total": 0, "count": 0}
    results["keep"][0]["billing_date"] = "2026-11-27"
    assert spend.next_30_days_spend(results, today) == {"total": 99.99, "count": 1}
    assert spend.next_30_days_spend({}, today) == {"total": 0, "count": 0}


def test_potential_savings_monthly_and_annual():
    results = {"suspend": [{"monthly_cost": 12.5}, {"monthly_cost": None}],
               "consider_non_renewal": [{"billing_cycle": "annual", "annual_cost": 120}],
               "keep": [{"monthly_cost": 50}]}
    assert spend.potential_monthly_savings(results) == 22.5


def test_duplicate_analysis_entries_are_only_one_bill():
    rec = dict(id=1, name="A", status="subscribed", monthly_cost=0.1, billing_date="2026-10-28")
    assert spend.next_30_days_spend({"keep": [rec], "snoozed": [rec]}, date(2026, 10, 28)) == {"total": 0.1, "count": 1}


def test_headline_links_action_count_and_uses_first_tone():
    for count in (1, 3):
        todo = [{"color": "cyan"}] * count
        result = spend.spend_headline({"monthly_total": 0}, {"bills": []}, 0, 0, todo)
        assert result["sub_parts"][-1] == {
            "text": f"{count} {'thing' if count == 1 else 'things'} to change.",
            "tone": "cyan", "emphasis": True, "href": "#todo"}
        assert "days, and " in "".join(p["text"] for p in result["sub_parts"])
