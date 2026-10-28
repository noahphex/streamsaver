from datetime import date, timedelta

import pytest

import db
from core import analysis, newsletter, service_manager, todo
from email_reports.report_builder import build_all_clear, build_digest, build_report


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()


def test_snoozes_match_name_and_action_and_expire(database):
    today = date.today()
    todo.snooze("Max", "suspend", today.isoformat())
    todo.snooze("Max", "suspend", (today + timedelta(days=1)).isoformat())
    todo.snooze("Untracked", "untracked", today.isoformat())
    todo.snooze("Expired", "uncertain", (today - timedelta(days=1)).isoformat())
    results = {"suspend": [{"name": "Max", "id": 1}, {"name": "Other"}],
               "uncertain": [{"name": "Max"}, {"name": "Expired"}],
               "untracked": [{"name": "Untracked", "shows": ["Show"]}], "snoozed": []}
    todo.apply_snoozes(results, today)
    assert results["suspend"] == [{"name": "Other"}]
    assert results["uncertain"] == [{"name": "Max"}, {"name": "Expired"}]
    assert results["untracked"] == []
    assert [(r["name"], r["action"], r["until"]) for r in results["snoozed"]] == [
        ("Max", "suspend", (today + timedelta(days=1)).isoformat()),
        ("Untracked", "untracked", today.isoformat())]
    with db.db_session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM todo_snoozes").fetchone()[0] == 2
    todo.unsnooze("Max", "suspend")
    results = {"suspend": [{"name": "Max"}]}
    todo.apply_snoozes(results, today)
    assert results["suspend"] == [{"name": "Max"}]


@pytest.mark.parametrize("billing, expected", [
    ("2026-11-19", "2026-11-19"), (None, "2026-11-27"),
    ("2026-10-01", "2026-11-27"), ("2026-10-28", "2026-11-27")])
def test_default_until(billing, expected):
    assert todo.default_until({"billing_date": billing}, date(2026, 10, 28)) == expected


@pytest.mark.parametrize("action", ["suspend", "resubscribe", "consider_non_renewal"])
def test_done_columns(database, action):
    today = date.today()
    service_id = service_manager.add_service("Max", status="suspended" if action == "resubscribe" else "subscribed", monthly_cost=10,
                                             billing_anchor_date="2020-01-01")
    rec = {"name": "Max", "billing_date": (today + timedelta(days=9)).isoformat()}
    todo.mark_done(service_id, action, rec, today)
    svc = service_manager.get_service(service_id)
    if action == "suspend":
        assert svc["status"] == "suspended"
    elif action == "resubscribe":
        assert svc["status"] == "subscribed"
        assert svc["billing_anchor_date"] == today.isoformat()
    else:
        assert svc["auto_renew"] == 0
        with db.db_session() as conn:
            row = conn.execute("SELECT * FROM todo_snoozes").fetchone()
        assert (row["service_name"], row["action"], row["until"]) == (
            "Max", action, rec["billing_date"])


def test_analysis_newsletter_and_reports_exclude_snoozed(database):
    service_manager.add_service("Hidden Service", status="subscribed", monthly_cost=10,
                                billing_anchor_date=date.today().isoformat())
    service_manager.add_service("Visible Service", status="subscribed", monthly_cost=10,
                                billing_anchor_date=date.today().isoformat())
    todo.snooze("Hidden Service", "suspend", date.today().isoformat())
    results = analysis.run_analysis()
    assert [r["name"] for r in results["suspend"]] == ["Visible Service"]
    filtered, _ = newsletter.filter_status_alerts_for_frequency(results)
    assert newsletter.has_status_alerts(filtered)
    for html in (build_digest(filtered)[0], build_all_clear(filtered)[0],
                 build_report("suspend", filtered["suspend"])[0]):
        assert "Hidden Service" not in html
    todo.snooze("Visible Service", "suspend", date.today().isoformat())
    filtered, _ = newsletter.filter_status_alerts_for_frequency(analysis.run_analysis())
    assert not newsletter.has_status_alerts(filtered)
    assert build_digest(filtered) is None


@pytest.mark.parametrize("action", todo.ACTIONABLE_KEYS)
def test_every_action_can_be_snoozed(database, action):
    todo.snooze("Max", action, date.today().isoformat())
    results = {action: [{"name": "Max"}], "keep": [{"name": "Max"}]}
    todo.apply_snoozes(results, date.today())
    assert results[action] == []
    assert results["keep"] == [{"name": "Max"}]
    assert results["snoozed"][0]["action"] == action


def test_turned_off_renewal_stays_hidden(database):
    today = date.today()
    billing = (today + timedelta(days=9)).isoformat()
    service_id = service_manager.add_service("Annual", status="subscribed", annual_cost=100,
                                             billing_cycle="annual", billing_anchor_date=billing)
    rec = analysis.run_analysis()["consider_non_renewal"][0]
    todo.mark_done(service_id, "consider_non_renewal", rec)
    results = analysis.run_analysis()
    assert results["consider_non_renewal"] == []
    assert results["snoozed"][0]["until"] == billing
