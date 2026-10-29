from copy import deepcopy
from datetime import date, timedelta
from unittest import mock

import pytest

import db
from core import analysis
from core.timeline import build_timeline, timeline_summary

TODAY = date(2026, 11, 2)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "timeline.db"))
    db.init_db()


def rec(id=1, offset=7, **values):
    return dict(dict(id=id, name=f"Service {id}", status="subscribed",
                     billing_cycle="monthly", monthly_cost=12.34,
                     billing_date=str(TODAY + timedelta(days=offset))), **values)


def show(title, offset=2, state="anticipated", service_id=1, **values):
    data = dict(trakt_id=title, title=title, show_state=state, service_id=service_id,
                next_air_date=str(TODAY + timedelta(days=offset)), **values)
    with db.db_session() as conn:
        if service_id is not None:
            conn.execute("INSERT OR IGNORE INTO services (id, name) VALUES (?, ?)",
                         (service_id, f"Service {service_id}"))
        columns = ", ".join(data)
        conn.execute(f"INSERT INTO shows ({columns}) VALUES ({', '.join('?' for _ in data)})",
                     list(data.values()))


def events(results, days=60):
    return [event for week in build_timeline(results, TODAY, days) for event in week["events"]]


def test_bill_window_cycles_subscription_and_dedupe(database):
    results = {"keep": [rec(offset=0), rec(2, 60), rec(3, -1), rec(4, 61),
                        rec(5, 3, status="suspended"), rec(6, 3, billing_date="bad")],
               "snoozed": [rec(offset=0), rec(7, 4, billing_cycle="annual", annual_cost=99.99)],
               "renew_soon": [rec(8, 2)], "consider_non_renewal": [rec(9, 2)],
               "suspend": [rec(10, 2)],
               "resubscribe": [rec(11, 2, status="unsubscribed")]}
    before = deepcopy(results)
    found = events(results)
    assert [e["service_id"] for e in found] == [1, 10, 8, 9, 7, 1, 10, 8, 9, 2]
    assert next(e for e in found if e["service_id"] == 7)["amount"] == 99.99
    assert found[0]["amount"] == 12.34
    assert results == before
    assert timeline_summary(build_timeline(results, TODAY)) == {"bills": 10, "amount": 211.05, "returns": 0}
    no_id = rec(id=None)
    assert len(events({"keep": [no_id], "snoozed": [no_id]})) == 2


def test_verdict_matches_analysis_and_todo_is_not_on_snoozed(database):
    with db.db_session() as conn:
        conn.executemany(
            "INSERT INTO services (id, name, status, monthly_cost, billing_anchor_date) VALUES (?, ?, 'subscribed', 10, ?)",
            [(1, "Service 1", "2026-11-09"), (2, "Service 2", "2026-11-09")])
    show("Before")
    show("On bill", 7)
    show("After", 8)
    show("Past", -1)
    show("Other", 8, service_id=2)
    with mock.patch.object(analysis, "date") as clock:
        clock.today.return_value = TODAY
        clock.fromisoformat.side_effect = date.fromisoformat
        results = analysis.run_analysis()
    assert [r["id"] for r in results["keep"]] == [1]
    assert [r["id"] for r in results["suspend"]] == [2]
    bills = [e for e in events(results) if e["type"] == "bill"]
    assert bills[0]["verdict"] == {"kind": "returns", "shows": [{"title": "Before", "date": "2026-11-04"},
                                                               {"title": "On bill", "date": "2026-11-09"}],
                                    "text": "2 shows return before this bill"}
    assert bills[1]["verdict"] == {"kind": "nothing_new", "text": "Nothing new before this bill"}
    assert bills[1]["todo"] == {"label": "Pause suggested"}
    assert "todo" not in bills[0]
    results["snoozed"] = results.pop("suspend")
    assert "todo" not in next(e for e in events(results) if e.get("service_id") == 2)


def test_annual_neutral_outside_analysis_window(database):
    # Matches ANNUAL_RENEWAL_EVAL_WINDOW (60): judged inside it, neutral beyond it.
    found = events({"keep": [rec(offset=60, billing_cycle="annual", annual_cost=120),
                             rec(2, 61, billing_cycle="annual", annual_cost=80)]}, days=90)
    assert found[0]["verdict"]["kind"] == "nothing_new"
    assert found[1]["verdict"] == {"kind": "prepaid", "text": "Annual renewal"}


def test_returns_exclusions_stale_dates_and_mapped_service(database):
    with db.db_session() as conn:
        conn.execute("INSERT INTO services (id, name, icon_path) VALUES (1, 'Bundle', 'bundle.png')")
    for state in ("abandoned", "ignored", "archived"):
        show(state, state=state)
    show("Past", -1)
    show("Watched later", last_watch_date="2026-11-06")
    show("Too far", 61)
    show("Mapped", primary_provider="Provider")
    show("Fallback", service_id=None, primary_provider="Independent")
    show("Today", 0, state="active")
    show("Last day", 60)
    found = events({})
    assert [e["title"] for e in found] == ["Today", "Fallback", "Mapped", "Last day"]
    mapped = next(e for e in found if e["title"] == "Mapped")
    assert (mapped["service_name"], mapped["icon_path"]) == ("Bundle", "bundle.png")
    assert next(e for e in found if e["title"] == "Fallback")["service_name"] == "Independent"


def test_week_labels_day_order(database):
    show("Same day", 0)
    timeline = build_timeline({"keep": [rec(offset=0, billing_cycle="annual"), rec(2, 7, billing_cycle="annual"),
                                                 rec(3, 15, billing_cycle="annual"), rec(4, 60, billing_cycle="annual")]}, TODAY)
    assert [w["label"] for w in timeline] == ["This week", "Next week", "Week of Nov 16", "Week of Dec 28"]
    assert [e["type"] for e in timeline[0]["events"]] == ["bill", "return"]
    assert build_timeline({}, TODAY)  # return only


def test_timeline_only_reads_database(database):
    show("Return")
    connect = db.get_connection

    def read_only_connection():
        conn = connect()
        conn.execute("PRAGMA query_only = ON")
        return conn

    with mock.patch.object(db, "get_connection", side_effect=read_only_connection):
        found = events({"suspend": [rec()]})
    assert [e["type"] for e in found] == ["return", "bill", "bill"]
    assert found[1]["verdict"]["text"] == "1 show returns before this bill"


def test_week_boundary_from_sunday(database):
    show("Following Monday", 14)
    sunday = date(2026, 11, 1)
    grouped = build_timeline({"keep": [rec(offset=-1, billing_cycle="annual"), rec(2, 0, billing_cycle="annual"),
                                     rec(3, 7, billing_cycle="annual")]}, sunday)
    assert [w["label"] for w in grouped] == ["This week", "Next week", "Week of Nov 9", "Week of Nov 16"]


def test_monthly_occurrences_intervals_totals_and_first_todo(database):
    show("First", 7)
    show("Second", 20)
    show("Active return", 25, state="active")
    results = {"suspend": [rec()]}
    bills = [e for e in events(results) if e["type"] == "bill"]
    assert [e["date"] for e in bills] == ["2026-11-09", "2026-12-09"]
    assert bills[1]["verdict"]["shows"] == [
        {"title": "Second", "date": "2026-11-22"},
        {"title": "Active return", "date": "2026-11-27"}]
    assert "todo" in bills[0] and "todo" not in bills[1]
    assert timeline_summary(build_timeline(results, TODAY))["amount"] == 24.68


@pytest.mark.parametrize("year,feb", [(2027, 28), (2028, 29)])
def test_month_end_preserves_anchor(database, year, feb):
    start = date(year, 1, 31)
    timeline = build_timeline({"keep": [rec(billing_date=str(start))]}, start, 60)
    assert [e["date"] for w in timeline for e in w["events"]] == [
        str(start), str(date(year, 2, feb)), str(date(year, 3, 31))]


def test_return_contexts(database):
    with db.db_session() as conn:
        conn.executemany("INSERT INTO services (id, name, status, billing_cycle) VALUES (?, ?, ?, ?)", [
            (1, "Monthly", "subscribed", "monthly"),
            (2, "Annual", "subscribed", "annual"),
            (3, "Paused", "unsubscribed", "monthly"),
            (4, "Other", "not_subscribed", "monthly")])
    show("Before", 2)
    show("After", 15)
    show("Annual", 3, service_id=2)
    show("Paused", 4, service_id=3)
    show("Other", 4, service_id=4)
    found = events({"keep": [rec()], "resubscribe": [rec(3)]}, days=20)
    returns = {e["title"]: e for e in found if e["type"] == "return"}
    assert returns["Before"]["context"] == "counts toward the Nov 9 bill"
    assert returns["After"]["context"] == "after your last bill in this window"
    assert returns["Annual"]["context"] == "annual · prepaid"
    assert returns["Paused"]["context"] == "not subscribed"
    assert returns["Paused"]["resubscribe_todo"] is True
    assert returns["Other"]["service_status"] == "not_subscribed"


def test_later_bill_empty_interval_and_annual_single_occurrence(database):
    show("On first bill", 7)
    bills = [e for e in events({"suspend": [rec()]}) if e["type"] == "bill"]
    assert bills[1]["verdict"] == {"kind": "unknown", "text": "Too early to tell"}
    assert len(events({"keep": [rec(billing_cycle="annual", annual_cost=100)]})) == 2  # one return, one bill


def test_next_bill_already_clamped_uses_stored_anchor(database):
    with db.db_session() as conn:
        conn.execute("INSERT INTO services (id, name, billing_anchor_date) VALUES (1, 'Month end', '2027-01-31')")
    timeline = build_timeline({"keep": [rec(billing_date="2027-02-28")]}, date(2027, 2, 1), 90)
    assert [e["date"] for w in timeline for e in w["events"]] == [
        "2027-02-28", "2027-03-31", "2027-04-30"]


def test_later_verdict_excludes_stale_watched_return(database):
    show("Already watched", 20, last_watch_date="2026-11-23")
    bills = [e for e in events({"keep": [rec()]}) if e["type"] == "bill"]
    assert bills[1]["verdict"]["kind"] == "unknown"


def test_only_first_bill_can_be_nothing_new(database):
    bills = [e for e in events({"keep": [rec()]}) if e["type"] == "bill"]
    assert [b["verdict"]["kind"] for b in bills] == ["nothing_new", "unknown"]


def test_snoozed_resubscribe_is_carried_to_return(database):
    show("Off return")
    with db.db_session() as conn:
        conn.execute("UPDATE services SET status = 'unsubscribed' WHERE id = 1")
    results = {"snoozed": [{"id": 1, "name": "Service 1", "action": "resubscribe", "until": "2026-11-27"}]}
    event = events(results)[0]
    assert event["snoozed_until"] == "2026-11-27"
    assert event["resubscribe_todo"] is False
    results["snoozed"][0]["action"] = "suspend"
    assert events(results)[0]["snoozed_until"] is None
    results["snoozed"][0].update(action="resubscribe", id=None)
    assert events(results)[0]["snoozed_until"] == "2026-11-27"
