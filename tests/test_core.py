"""
tests/test_core.py
Streamsaver — billing roll-forward, score decay, token refresh and device login checks.

Run:  python -m unittest discover tests      (inside the container: docker exec streamsaver ...)
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from core import analysis, show_tracker
from core.analysis import _add_months, _advance_billing_date
from core.trakt_client import TraktClient, poll_device_token


class BillingDateTests(unittest.TestCase):
    def test_month_end_anchor_clamps_and_keeps_its_day(self):
        self.assertEqual(_add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(_add_months(date(2026, 1, 31), 2), date(2026, 3, 31))
        self.assertEqual(_advance_billing_date(date(2026, 1, 31), "monthly", date(2026, 2, 15)),
                         (date(2026, 2, 28), False))

    def test_leap_day_annual_anchor(self):
        self.assertEqual(_advance_billing_date(date(2028, 2, 29), "annual", date(2028, 6, 1)),
                         (date(2029, 2, 28), False))

    def test_old_monthly_anchor_is_not_stale(self):
        self.assertEqual(_advance_billing_date(date(2026, 6, 19), "monthly", date(2026, 10, 28)),
                         (date(2026, 11, 19), False))

    def test_annual_two_cycles_behind_is_stale(self):
        _, uncertain = _advance_billing_date(date(2024, 1, 10), "annual", date(2026, 6, 1))
        self.assertTrue(uncertain)


def _resp(status, payload=None):
    return mock.Mock(status_code=status, headers={}, text="", json=lambda: payload)


class TraktAuthTests(unittest.TestCase):
    def test_refresh_survives_a_failed_token_save(self):
        def broken_save(tokens):
            raise OSError("disk full")

        client = TraktClient(client_id="id", access_token="old", client_secret="sec",
                             refresh_token="old-r", username="u", token_refresh_callback=broken_save)
        with mock.patch.object(client._session, "get", side_effect=[_resp(401), _resp(200, {"ok": 1})]), \
             mock.patch("core.trakt_client.requests.post",
                        return_value=_resp(200, {"access_token": "new", "refresh_token": "new-r"})):
            self.assertEqual(client.get_show_summary("1"), {"ok": 1})
        self.assertEqual(client._session.headers["Authorization"], "Bearer new")

    def test_device_token_pending_approved_and_expired(self):
        with mock.patch("core.trakt_client.requests.post", return_value=_resp(400)):
            self.assertIsNone(poll_device_token("id", "sec", "dev"))
        with mock.patch("core.trakt_client.requests.post",
                        return_value=_resp(200, {"access_token": "a", "refresh_token": "r"})):
            self.assertEqual(poll_device_token("id", "sec", "dev")["access_token"], "a")
        with mock.patch("core.trakt_client.requests.post", return_value=_resp(410)):
            with self.assertRaisesRegex(RuntimeError, "expired"):
                poll_device_token("id", "sec", "dev")


class TokenRaceTests(unittest.TestCase):
    def test_401_adopts_tokens_another_process_already_refreshed(self):
        client = TraktClient(client_id="id", access_token="stale", client_secret="sec",
                             refresh_token="dead-r", username="u",
                             token_loader=lambda: {"access_token": "fresh", "refresh_token": "fresh-r"})
        with mock.patch.object(client._session, "get", side_effect=[_resp(401), _resp(200, {"ok": 1})]), \
             mock.patch("core.trakt_client.requests.post") as post:
            self.assertEqual(client.get_show_summary("1"), {"ok": 1})
        post.assert_not_called()  # the dead refresh token was never spent
        self.assertEqual(client._session.headers["Authorization"], "Bearer fresh")
        self.assertEqual(client.refresh_token, "fresh-r")

    def test_saving_tokens_keeps_newer_fields_on_disk(self):
        import setup_credentials as sc

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(sc, "CONFIG_DIR", tmp), \
             mock.patch.object(sc, "CRED_FILE", os.path.join(tmp, "credentials.enc")), \
             mock.patch.object(sc, "SALT_FILE", os.path.join(tmp, ".salt")):
            sc.encrypt_credentials({"gmail_app_password": "changed-in-web-ui",
                                    "trakt_access_token": "old", "trakt_refresh_token": "old-r"}, "pw-12345", quiet=True)
            merged = sc.save_trakt_tokens("pw-12345", "new", "new-r")
            on_disk = sc.load_credentials("pw-12345")
            self.assertFalse(os.path.exists(sc.CRED_FILE + ".tmp"))
        self.assertEqual(on_disk, merged)
        self.assertEqual(on_disk["gmail_app_password"], "changed-in-web-ui")
        self.assertEqual((on_disk["trakt_access_token"], on_disk["trakt_refresh_token"]), ("new", "new-r"))


class _FakeTrakt:
    def get_all_show_history(self, start_at):
        return []


class DbTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp.name, "test.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig_path
        self._tmp.cleanup()

    def _add_show(self, trakt_id, state, score, last_watch, next_air=None, service_id=None):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO shows (trakt_id, title, show_state, interest_score, last_watch_date, "
                "next_air_date, service_id) VALUES (?,?,?,?,?,?,?)",
                (trakt_id, f"Show {trakt_id}", state, score, last_watch, next_air, service_id),
            )

    def _state(self, trakt_id):
        with db.db_session() as conn:
            row = conn.execute("SELECT show_state, interest_score FROM shows WHERE trakt_id = ?",
                               (trakt_id,)).fetchone()
        return row["show_state"], row["interest_score"]

    def test_unwatched_shows_decay_to_dormant(self):
        past = (date.today() - timedelta(days=150)).isoformat()
        future = (date.today() + timedelta(days=20)).isoformat()
        self._add_show("1", "active", 20, past)
        self._add_show("2", "anticipated", 10, past, next_air=past)
        self._add_show("3", "anticipated", 10, past, next_air=future)

        show_tracker.sync_watched_shows(_FakeTrakt())

        self.assertEqual(self._state("1"), ("dormant", 0))
        self.assertEqual(self._state("2"), ("dormant", 0))
        self.assertEqual(self._state("3")[0], "anticipated")

    def test_aired_show_does_not_keep_a_service(self):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO services (name, status, monthly_cost, billing_cycle, billing_anchor_date) "
                "VALUES ('Netflix', 'subscribed', 15.0, 'monthly', ?)",
                ((date.today() - timedelta(days=100)).isoformat(),),
            )
            service_id = conn.execute("SELECT id FROM services").fetchone()["id"]
        self._add_show("9", "anticipated", 10, None,
                       next_air=(date.today() - timedelta(days=60)).isoformat(), service_id=service_id)

        results = analysis.run_analysis()

        self.assertEqual([s["name"] for s in results["suspend"]], ["Netflix"])


    def test_returning_favourite_on_unsubscribed_service_suggests_resubscribe(self):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO services (name, status, monthly_cost, billing_cycle, billing_anchor_date) "
                "VALUES ('Paramount+', 'unsubscribed', 8.0, 'monthly', NULL)")  # unsubscribed: usually no date
            sid = conn.execute("SELECT id FROM services").fetchone()["id"]
        # 28 is the highest score the tracker can produce
        self._add_show("cup", "anticipated", 28, date.today().isoformat(),
                       next_air=(date.today() + timedelta(days=2)).isoformat(), service_id=sid)

        results = analysis.run_analysis()

        self.assertEqual([r["name"] for r in results["resubscribe"]], ["Paramount+"])

    def test_untracked_providers_are_grouped_and_weekly(self):
        from core.newsletter import filter_status_alerts_for_frequency, mark_status_alerts_sent
        from email_reports.report_builder import build_digest

        self._add_show("a", "active", 20, date.today().isoformat())
        self._add_show("b", "anticipated", 15, date.today().isoformat(),
                       next_air=(date.today() + timedelta(days=5)).isoformat())
        self._add_show("c", "dormant", 0, "2025-01-01")  # not watching: ignored
        with db.db_session() as conn:
            conn.execute("UPDATE shows SET primary_provider = 'Crunchyroll'")

        results = analysis.run_analysis()
        self.assertEqual(results["untracked"], [{"name": "Crunchyroll", "shows": ["Show a", "Show b"]}])

        html, _ = build_digest(results)
        self.assertIn("Watching on Services You Don", html)
        self.assertIn("Show a, Show b", html)

        mark_status_alerts_sent()  # weekly default: held back until 7 days pass
        held, _ = filter_status_alerts_for_frequency(results)
        self.assertEqual(held["untracked"], [])

    def test_free_service_is_never_flagged(self):
        with db.db_session() as conn:
            conn.execute(
                "INSERT INTO services (name, status, monthly_cost, annual_cost, billing_cycle, billing_anchor_date) "
                "VALUES ('Apple TV', 'subscribed', 0, 0, 'annual', ?)",
                ((date.today() + timedelta(days=10)).isoformat(),))

        results = analysis.run_analysis()

        flagged = [r["name"] for key in ("suspend", "consider_non_renewal", "uncertain") for r in results[key]]
        self.assertEqual(flagged, [])
        self.assertEqual([r["name"] for r in results["keep"]], ["Apple TV"])

    def test_relink_fixes_stale_service_links(self):
        with db.db_session() as conn:
            conn.execute("INSERT INTO services (name, status, tmdb_provider_id) VALUES ('Amazon Prime', 'subscribed', 9)")
            conn.execute("INSERT INTO services (name, status, tmdb_provider_id) VALUES ('Apple TV', 'subscribed', 350)")
            prime = conn.execute("SELECT id FROM services WHERE name = 'Amazon Prime'").fetchone()["id"]
        self._add_show("silo", "anticipated", 23, None, service_id=prime)
        self._add_show("orphan", "active", 20, None, service_id=prime)
        self._add_show("manual", "active", 20, None, service_id=prime)  # no provider info: keep link
        with db.db_session() as conn:
            conn.execute("UPDATE shows SET primary_provider_id = 350, primary_provider = 'Apple TV' WHERE trakt_id = 'silo'")
            conn.execute("UPDATE shows SET primary_provider_id = 337, primary_provider = 'Disney Plus' WHERE trakt_id = 'orphan'")

        self.assertEqual(show_tracker.backfill_service_ids(), 2)

        with db.db_session() as conn:
            links = dict(conn.execute(
                "SELECT shows.trakt_id, s.name FROM shows LEFT JOIN services s ON s.id = shows.service_id").fetchall())
        self.assertEqual(links, {"silo": "Apple TV", "orphan": None, "manual": "Amazon Prime"})

    def test_sync_prefers_a_provider_you_have(self):
        with db.db_session() as conn:
            conn.execute("INSERT INTO services (name, status, tmdb_provider_id) VALUES ('Peacock Premium Plus', 'subscribed', 387)")

        class Trakt:
            def get_show_summary(self, trakt_id): return {}
            def get_next_episode(self, trakt_id): return None

        class Tmdb:
            def search_tv_show(self, title): return 215943
            def get_show_providers(self, tmdb_id):
                return [{"provider_id": 257, "provider_name": "fuboTV"},
                        {"provider_id": 387, "provider_name": "Peacock Premium Plus"}]

        now = show_tracker.datetime.now(show_tracker.timezone.utc)
        show_tracker._upsert_show({"trakt_id": "200623", "title": "The Traitors", "events": [now, now, now],
                                   "season": 4, "last_at_raw": now.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                                  Trakt(), Tmdb(), "US")

        with db.db_session() as conn:
            row = conn.execute("SELECT sh.primary_provider, s.name FROM shows sh "
                               "JOIN services s ON s.id = sh.service_id WHERE trakt_id = '200623'").fetchone()
        self.assertEqual((row["primary_provider"], row["name"]), ("Peacock Premium Plus", "Peacock Premium Plus"))

    def test_restore_relinks_shows_to_services(self):
        import json
        from core.backup import import_backup

        with db.db_session() as conn:
            conn.execute("INSERT INTO services (name, status, tmdb_provider_id) VALUES ('Hulu', 'subscribed', 15)")
            sid = conn.execute("SELECT id FROM services").fetchone()["id"]
        self._add_show("5", "active", 20, date.today().isoformat(), service_id=sid)
        with db.db_session() as conn:
            conn.execute("UPDATE shows SET primary_provider_id = 15, primary_provider = 'Hulu'")
        path = os.path.join(self._tmp.name, "backup.json")
        with open(path, "w") as fh:
            json.dump({"version": 1, "settings": {},
                       "services": [{"name": "Hulu", "status": "subscribed", "tmdb_provider_id": 15}]}, fh)

        with mock.patch("core.service_manager.backfill_service_icons", return_value=0):
            import_backup(path)

        with db.db_session() as conn:
            row = conn.execute("SELECT s.name FROM shows JOIN services s ON s.id = shows.service_id").fetchone()
        self.assertEqual(row["name"], "Hulu")


class EmailTests(unittest.TestCase):
    def test_titles_are_escaped(self):
        from email_reports.report_builder import _build_resubscribe_content

        html, preheader, _ = _build_resubscribe_content([{
            "name": "Max <b>", "billing_date": "2026-11-01", "monthly_cost": 10.0, "annual_cost": 0,
            "shows": [{"title": "Love & <Death>", "next_air_date": "2026-11-02"}],
        }])
        self.assertNotIn("<Death>", html)
        self.assertIn("Love &amp; &lt;Death&gt;", html)
        self.assertNotIn("Max <b>", html)

    def test_all_clear_lists_kept_services(self):
        from email_reports.report_builder import build_all_clear

        html, _ = build_all_clear({"keep": [{"name": "Hulu", "reason": "1 active show(s)."}]})
        self.assertIn("All Clear", html)
        self.assertIn("Hulu", html)
        self.assertIn("1 active show(s).", html)


if __name__ == "__main__":
    unittest.main()
