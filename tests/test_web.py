"""
tests/test_web.py
Streamsaver — web sign-in gate, cross-site POST block, and Trakt device login.

Run:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import setup_credentials as sc
import web.app as webapp

PASSPHRASE = "test-passphrase-123"


class WebTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cred_file = os.path.join(self._tmp.name, "credentials.enc")
        self._patches = [
            mock.patch.object(db, "DB_PATH", os.path.join(self._tmp.name, "test.db")),
            mock.patch.object(sc, "CONFIG_DIR", self._tmp.name),
            mock.patch.object(sc, "CRED_FILE", cred_file),
            mock.patch.object(sc, "SALT_FILE", os.path.join(self._tmp.name, ".salt")),
            mock.patch.object(webapp, "CRED_FILE", cred_file),
            mock.patch.dict(os.environ, {"STREAMSAVER_PASSPHRASE": PASSPHRASE}),
        ]
        for p in self._patches:
            p.start()
        sc.encrypt_credentials({"trakt_client_id": "cid", "trakt_client_secret": "sec",
                                "trakt_access_token": "old", "trakt_refresh_token": "old-r",
                                "gmail_sender": "sender@example.test", "gmail_app_password": "keep-me"}, PASSPHRASE, quiet=True)
        self.client = webapp.create_app().test_client()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _post(self, url, **kwargs):
        with self.client.session_transaction() as session:
            token = session.setdefault("csrf_token", "test-csrf-token")
        data = kwargs.setdefault("data", {})
        data["csrf_token"] = token
        return self.client.post(url, **kwargs)

    def _sign_in(self):
        r = self._post("/unlock", data={"passphrase": PASSPHRASE})
        self.assertEqual(r.status_code, 302)

    def test_todo_routes_validate_and_complete_actions(self):
        from datetime import date
        from core.service_manager import add_service, get_service

        self._sign_in()
        service_id = add_service("Max", status="subscribed", monthly_cost=10,
                                 billing_anchor_date=date.today().isoformat())
        self.assertEqual(self._post(f"/todo/{service_id}/done/bad").status_code, 400)
        self.assertEqual(self._post("/todo/99999/done/suspend").status_code, 302)
        for action in ("suspend", "resubscribe", "consider_non_renewal"):
            with mock.patch.object(webapp, "run_analysis", return_value={action: [{"id": service_id, "name": "Max"}]}):
                response = self._post(f"/todo/{service_id}/done/{action}")
            self.assertEqual((response.status_code, response.headers["Location"]), (302, "/"))
        svc = get_service(service_id)
        self.assertEqual((svc["status"], svc["billing_anchor_date"], svc["auto_renew"]),
                         ("subscribed", date.today().isoformat(), 0))
        for data in ({"name": "Max", "action": "bad"},
                     {"name": "Missing", "action": "suspend"},
                     {"name": "Max", "action": "untracked"}, {}):
            self.assertEqual(self._post("/todo/snooze", data=data).status_code, 400)
        self.assertEqual(self._post("/todo/unsnooze", data={"name": "Max", "action": "bad"}).status_code, 400)
        self.assertEqual(self.client.get(f"/todo/{service_id}/done/suspend").status_code, 405)

    def test_csrf_public_forms_and_rotation(self):
        self.assertEqual(self.client.post("/unlock").status_code, 400)
        self.client.get("/unlock")
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        for path in ("/unlock", "/setup"):
            for data in ({}, {"csrf_token": "wrong"}, {"csrf_token": "☃"}):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Your session expired. Reload the page and try again.", response.data)
        response = self.client.post("/unlock", data={"passphrase": PASSPHRASE, "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotEqual(session["csrf_token"], token)
        self.assertEqual(self.client.post("/lock", data={"csrf_token": token}).status_code, 400)

    def test_first_run_setup_requires_csrf(self):
        with mock.patch.object(webapp, "CRED_FILE", os.path.join(self._tmp.name, "new.enc")):
            self.assertEqual(self.client.get("/setup").status_code, 200)
            self.assertEqual(self.client.post("/setup").status_code, 400)
            with self.client.session_transaction() as session:
                token = session["csrf_token"]
            response = self.client.post("/setup", data={"csrf_token": token, "passphrase": "short"})
            self.assertEqual(response.status_code, 302)
            with self.client.session_transaction() as session:
                self.assertIn(("error", "Passphrase must be at least 8 characters."), session["_flashes"])

    def test_csrf_protects_all_mutating_methods_and_accepts_header(self):
        self._sign_in()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for headers in ({}, {"X-CSRF-Token": "wrong"}):
                self.assertEqual(self.client.open("/settings", method=method, headers=headers).status_code, 400)
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        response = self.client.post("/settings", data={"notification_email": "me@example.com"},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(webapp._get_setting("notification_email"), "me@example.com")

    def test_done_rejects_stale_suggestions_without_changes(self):
        from core.service_manager import add_service, get_service
        self._sign_in()
        service_id = add_service("Max", status="subscribed")
        before = get_service(service_id)
        with mock.patch.object(webapp, "run_analysis", return_value={"suspend": []}):
            response = self._post(f"/todo/{service_id}/done/suspend")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_service(service_id), before)
        with self.client.session_transaction() as session:
            self.assertNotIn("todo_undo", session)
            self.assertIn(("error", "That suggestion is no longer on your to-do list."), session["_flashes"])

    def test_done_notice_persists_and_undo_restores(self):
        from core.service_manager import add_service, get_service
        self._sign_in()
        service_id = add_service("Max", status="subscribed", monthly_cost=10,
                                 billing_anchor_date=date.today().isoformat())
        before = get_service(service_id)
        self._post(f"/todo/{service_id}/done/suspend")
        for _ in range(2):
            page = self.client.get("/").data.decode()
            self.assertIn("Marked Max as paused.", page)
            self.assertIn('aria-label="Undo: Max"', page)
            self.assertNotIn("Change it on the Services page.", page)
        page = self._post("/todo/undo", data={"service_id": service_id}, follow_redirects=True).data.decode()
        self.assertEqual(get_service(service_id), before)
        self.assertIn("Undid Max.", page)
        self.assertNotIn("Marked Max as paused.", page)
        page = self._post("/todo/undo", data={"service_id": service_id}, follow_redirects=True).data.decode()
        self.assertIn("Nothing to undo.", page)

    def test_renewal_undo_removes_snooze_after_service_rename(self):
        from datetime import timedelta
        from core.service_manager import add_service, get_service, update_service
        self._sign_in()
        service_id = add_service("Annual", status="subscribed", annual_cost=100,
                                 billing_cycle="annual",
                                 billing_anchor_date=(date.today() + timedelta(days=9)).isoformat())
        before = get_service(service_id)
        self._post(f"/todo/{service_id}/done/consider_non_renewal")
        page = self.client.get("/").data.decode()
        self.assertTrue("Marked Annual as having renewal turned off." in page)
        update_service(service_id, name="Renamed")
        self.assertEqual(self._post("/todo/undo", data={"service_id": service_id}).status_code, 302)
        restored = get_service(service_id)
        for key in ("status", "billing_anchor_date", "auto_renew"):
            self.assertEqual(restored[key], before[key])
        self.assertEqual(restored["name"], "Renamed")
        with db.db_session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM todo_snoozes").fetchone()[0], 0)

    def test_undo_only_matches_latest_service_and_expires(self):
        from core.service_manager import add_service, get_service
        self._sign_in()
        first = add_service("Max", status="subscribed", monthly_cost=10,
                            billing_anchor_date=date.today().isoformat())
        second = add_service("Hulu", status="subscribed", monthly_cost=10,
                             billing_anchor_date=date.today().isoformat())
        self._post(f"/todo/{first}/done/suspend")
        self._post(f"/todo/{second}/done/suspend")
        for data in ({}, {"service_id": first}):
            page = self._post("/todo/undo", data=data, follow_redirects=True).data.decode()
            self.assertIn("Nothing to undo.", page)
        with self.client.session_transaction() as session:
            self.assertEqual(session["todo_undo"]["service_id"], second)
            created = session["todo_undo"]["timestamp"]
        with mock.patch.object(webapp.time, "time", return_value=created + 599):
            self.assertTrue("Marked Hulu as paused." in self.client.get("/").data.decode())
        with mock.patch.object(webapp.time, "time", return_value=created + 600):
            self.assertFalse("Marked Hulu as paused." in self.client.get("/").data.decode())
            page = self._post("/todo/undo", data={"service_id": second}, follow_redirects=True).data.decode()
        self.assertIn("Nothing to undo.", page)
        self.assertNotIn("Marked Hulu as paused.", page)
        self.assertEqual(get_service(second)["status"], "suspended")
        with self.client.session_transaction() as session:
            self.assertNotIn("todo_undo", session)

    def test_invalid_undo_records_are_cleared(self):
        import time
        self._sign_in()
        valid = {"service_id": 1, "name": "Max", "action": "suspend",
                 "prev": {"status": "subscribed", "billing_anchor_date": None, "auto_renew": 1},
                 "timestamp": time.time()}
        records = [{}, "bad", {**valid, "action": "bad"}, {**valid, "prev": {}},
                   {key: value for key, value in valid.items() if key != "timestamp"}]
        for record in records:
            with self.subTest(record=record):
                with self.client.session_transaction() as session:
                    session["todo_undo"] = record
                response = self._post("/todo/undo", data={"service_id": 1})
                self.assertEqual(response.status_code, 302)
                with self.client.session_transaction() as session:
                    self.assertNotIn("todo_undo", session)
                    self.assertIn(("info", "Nothing to undo."), session["_flashes"])

    def test_todo_snooze_and_unsnooze_dashboard(self):
        from datetime import date
        from core.service_manager import add_service

        self._sign_in()
        add_service("Max", status="subscribed", monthly_cost=10,
                    billing_anchor_date=date.today().isoformat())
        page = self.client.get("/").data.decode()
        self.assertIn('aria-label="Paused it: Max"', page)
        self.assertIn('aria-label="Not now: Max"', page)
        self.assertIn('class="todo-count" aria-hidden="true">1</span>', page)
        self.assertEqual(page.count("Could save $10.00/mo"), 1)
        data = {"name": "Max", "action": "suspend"}
        page = self._post("/todo/snooze", data=data, follow_redirects=True).data.decode()
        self.assertNotIn('class="todo-link"', page)
        self.assertIn("Snoozed (1)", page)
        self.assertIn("Snoozed Max until", page)
        self.assertIn('aria-label="Undo snooze: Max"', page)
        self.assertNotIn("0 changes", page)
        page = self._post("/todo/unsnooze", data=data, follow_redirects=True).data.decode()
        self.assertIn('aria-label="Paused it: Max"', page)
        self.assertNotIn("Snoozed (1)", page)

    def test_snooze_uses_server_date_and_services_hide_advice(self):
        from datetime import date, timedelta
        from core.service_manager import add_service

        self._sign_in()
        billing = (date.today() + timedelta(days=9)).isoformat()
        service_id = add_service("Max", status="subscribed", monthly_cost=10,
                                 billing_anchor_date=billing)
        response = self._post("/todo/snooze", data={"name": "Max", "action": "suspend",
                                                        "until": "2099-01-01"})
        self.assertEqual(response.status_code, 302)
        with db.db_session() as conn:
            self.assertEqual(conn.execute("SELECT until FROM todo_snoozes").fetchone()[0], billing)
        self.assertNotIn('class="pill yellow">Pause', self.client.get("/services").data.decode())
        self.assertNotIn('<h2>Pause</h2>', self.client.get(f"/services/{service_id}/edit").data.decode())

    def test_untracked_todo_has_not_now_without_done(self):
        self._sign_in()
        with db.db_session() as conn:
            conn.execute("INSERT INTO shows (trakt_id, title, show_state, primary_provider) "
                         "VALUES ('u', 'Example show', 'active', 'Untracked')")
        page = self.client.get("/").data.decode()
        self.assertIn('aria-label="Not now: Untracked"', page)
        self.assertNotIn('/done/', page)
        page = self._post("/todo/snooze", data={"name": "Untracked", "action": "untracked"},
                                follow_redirects=True).data.decode()
        self.assertNotIn('class="todo-link"', page)
        self.assertIn("Snoozed (1)", page)

    def test_pages_need_sign_in_but_health_does_not(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        r = self.client.get("/settings")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/unlock"))
        self.assertEqual(self._post("/settings", data={"notification_email": "x@evil.test"}).status_code, 403)

        self._post("/unlock", data={"passphrase": "wrong-passphrase"})
        self.assertEqual(self.client.get("/settings").status_code, 302)  # wrong passphrase: still out

        self._sign_in()
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self._post("/lock")
        self.assertEqual(self.client.get("/settings").status_code, 302)

    def test_cross_site_post_is_rejected_even_when_signed_in(self):
        self._sign_in()
        r = self._post("/settings", data={"notification_email": "x@evil.test"},
                             headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self._post("/settings", data={"notification_email": "me@example.com"},
                             headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(webapp._get_setting("notification_email"), "me@example.com")

    def test_only_discord_webhooks_are_accepted(self):
        self._sign_in()
        self._post("/settings", data={"discord_webhook_url": "http://127.0.0.1/admin"})
        self.assertEqual(webapp._get_setting("discord_webhook_url"), "")
        self._post("/settings", data={"discord_webhook_url": "https://discord.com/api/webhooks/1/abc"})
        self.assertEqual(webapp._get_setting("discord_webhook_url"), "https://discord.com/api/webhooks/1/abc")

    def test_device_login_saves_new_tokens_and_keeps_other_credentials(self):
        self._sign_in()
        code = {"device_code": "dev", "user_code": "ABCD1234",
                "verification_url": "https://trakt.tv/activate", "expires_in": 600}
        with mock.patch.object(webapp, "request_device_code", return_value=code):
            self._post("/credentials/trakt/device/start")
        self.assertIn(b"ABCD1234", self.client.get("/credentials").data)

        with mock.patch.object(webapp, "poll_device_token", return_value=None):
            r = self._post("/credentials/trakt/device/finish", follow_redirects=True)
        self.assertIn(b"Not approved yet", r.data)

        with mock.patch.object(webapp, "poll_device_token",
                               return_value={"access_token": "new", "refresh_token": "new-r"}):
            self._post("/credentials/trakt/device/finish")
        saved = sc.load_credentials(PASSPHRASE)
        self.assertEqual((saved["trakt_access_token"], saved["trakt_refresh_token"]), ("new", "new-r"))
        self.assertEqual(saved["gmail_app_password"], "keep-me")


    def test_saving_trakt_details_without_pin_keeps_tokens(self):
        self._sign_in()
        self._post("/credentials/trakt", data={"trakt_client_id": "cid2", "trakt_username": "noah"})
        saved = sc.load_credentials(PASSPHRASE)
        self.assertEqual((saved["trakt_client_id"], saved["trakt_access_token"]), ("cid2", "old"))

    def test_email_form_without_report_field_keeps_report_address(self):
        self._sign_in()
        webapp._set_setting("notification_email", "reports@example.com")
        self._post("/credentials/email", data={"gmail_sender": "sender@example.test"})
        self.assertEqual(webapp._get_setting("notification_email"), "reports@example.com")

    def test_gmail_test_failure_is_reported_not_raised(self):
        self._sign_in()
        with mock.patch.object(webapp, "check_login", side_effect=RuntimeError("Gmail login failed (closed).")):
            r = self._post("/credentials/test/email", follow_redirects=True)
        self.assertIn(b"Connection test failed. Try again.", r.data)
        self.assertNotIn(b"closed", r.data)

    def test_todo_items_and_dates(self):
        items = webapp._todo_items({
            "resubscribe": [{"name": "Paramount+", "billing_date": "",
                             "shows": [{"title": "Cupertino", "next_air_date": "2026-10-30"}]}],
            "untracked": [{"name": "Crunchyroll", "shows": ["Frieren"]}],
            "keep": [{"name": "Hulu", "reason": "x"}],
        })
        self.assertEqual([(i["label"], i["name"]) for i in items],
                         [("Resubscribe", "Paramount+"), ("Add service", "Crunchyroll")])
        self.assertIn("Cupertino returns Oct 30", items[0]["why"])
        self.assertEqual(webapp._money(1303.91), "$1,303.91")
        self.assertEqual(webapp._local_time(""), "never")


    def test_unmark_restores_the_state_before_abandon(self):
        self._sign_in()
        with db.db_session() as conn:
            conn.execute("INSERT INTO shows (trakt_id, title, show_state, interest_score) VALUES ('t1', 'Silo', 'anticipated', 23)")
        self._post("/shows/t1/state", data={"state": "abandoned"})
        self._post("/shows/t1/state", data={"state": "abandoned"})  # double-click keeps the original
        self._post("/shows/t1/state", data={"state": "restore"})
        with db.db_session() as conn:
            row = conn.execute("SELECT show_state, abandoned_override FROM shows WHERE trakt_id = 't1'").fetchone()
        self.assertEqual((row["show_state"], row["abandoned_override"]), ("anticipated", 0))

    def test_credential_test_result_is_remembered(self):
        self._sign_in()
        with mock.patch.object(webapp, "check_login", return_value=None):
            self._post("/credentials/test/email")
        page = self.client.get("/credentials").data
        self.assertIn(b"Works", page)
        self.assertTrue(webapp._last_credential_test("email")["ok"])

    def test_todo_item_links_and_cost(self):
        items = webapp._todo_items({"resubscribe": [{"id": 7, "name": "Paramount+", "billing_cycle": "monthly",
                                                       "monthly_cost": 13.99, "billing_date": "",
                                                       "shows": [{"title": "Cupertino", "next_air_date": "2026-10-30"}]}]})
        self.assertEqual((items[0]["service_id"], items[0]["cost"]), (7, "$13.99/mo"))
        self.assertTrue(items[0]["why"].startswith("Cupertino returns Oct 30"))

    def test_todo_summary_counts_and_monthly_savings(self):
        self.assertEqual(webapp._todo_summary({}), "")
        self.assertEqual(webapp._todo_summary({"resubscribe": [{"monthly_cost": 20}]}), "1 change")
        results = {
            "suspend": [{"monthly_cost": 12.50}, {"monthly_cost": None}],
            "consider_non_renewal": [{"billing_cycle": "annual", "annual_cost": 120, "monthly_cost": 999}],
            "renew_soon": [{"billing_cycle": "annual", "annual_cost": 240}],
            "resubscribe": [{"monthly_cost": 30}],
            "keep": [{"monthly_cost": 50}],
        }
        self.assertEqual(webapp._todo_summary(results), "5 changes")
        self.assertEqual(webapp._todo_summary({"suspend": [{"monthly_cost": 0}]}), "1 change")

    def test_connection_issues_use_stored_state(self):
        self._sign_in()
        app = self.client.application
        webapp._record_credential_test("trakt", False, webapp._CREDENTIAL_ERRORS["auth"])
        webapp._record_credential_test("email", True, "Gmail works.")
        with app.test_request_context():
            issues = webapp._connection_issues()
        self.assertEqual(issues, [
            {"provider": "trakt", "name": "Trakt", "message": webapp._CREDENTIAL_ERRORS["auth"],
             "impact": "Show sync is paused until this works.", "href": "/credentials#trakt"},
            {"provider": "tmdb", "name": "TMDb", "message": webapp._CREDENTIAL_ERRORS["missing"],
             "impact": "New shows can't be matched to services.", "href": "/credentials#tmdb"},
        ])
        webapp._record_credential_test("tmdb", False, webapp._CREDENTIAL_ERRORS["network"])
        with app.test_request_context():
            self.assertEqual(webapp._connection_issues()[1]["message"], webapp._CREDENTIAL_ERRORS["network"])
        webapp._record_credential_test("tmdb", True, "TMDb works.")
        app.config["CREDS"]["tmdb_api_key"] = "test-key"
        webapp._record_credential_test("trakt", True, "Trakt works.")
        with app.test_request_context():
            self.assertEqual(webapp._connection_issues(), [])
        app.config["CREDS"]["gmail_app_password"] = ""
        with app.test_request_context():
            self.assertEqual(webapp._connection_issues(), [
                {"provider": "email", "name": "Gmail", "message": webapp._CREDENTIAL_ERRORS["missing"],
                 "impact": "Reports can't be emailed.", "href": "/credentials#gmail"},
            ])

    def test_connection_issues_require_every_required_key(self):
        app = self.client.application
        configured = {key: "test-value" for keys in webapp._CREDENTIAL_REQUIRED_KEYS.values() for key in keys}
        with app.test_request_context():
            app.config["CREDS"] = configured
            self.assertEqual(webapp._connection_issues(), [])
            for provider, keys in webapp._CREDENTIAL_REQUIRED_KEYS.items():
                for key in keys:
                    with self.subTest(provider=provider, key=key):
                        app.config["CREDS"] = {**configured, key: ""}
                        issues = webapp._connection_issues()
                        self.assertEqual([issue["provider"] for issue in issues], [provider])
                        self.assertEqual(issues[0]["message"], webapp._CREDENTIAL_ERRORS["missing"])

    def test_dashboard_banner_summary_and_all_clear(self):
        self._sign_in()
        results = {"suspend": [{"id": 7, "name": "Hulu", "monthly_cost": 15,
                                "reason": "No recent viewing.", "billing_date": "2026-10-30"}]}
        webapp._record_credential_test("email", False, webapp._CREDENTIAL_ERRORS["network"])
        with mock.patch.object(webapp, "run_analysis", return_value=results), \
                mock.patch.object(webapp, "_trakt_client") as trakt, \
                mock.patch.object(webapp, "_tmdb_client") as tmdb, \
                mock.patch.object(webapp, "check_login") as gmail:
            page = self.client.get("/").data.decode()
        for client in (trakt, tmdb, gmail):
            client.assert_not_called()
        self.assertIn('<section class="spend-headline">', page)
        self.assertNotIn("Streaming Cost Control", page)
        self.assertIn('class="todo-count" aria-hidden="true">1</span>', page)
        self.assertEqual(page.count("Could save $15.00/mo"), 1)
        self.assertNotIn("Synced never", page)
        self.assertIn('role="status" aria-label="Connections need attention"', page)
        self.assertLess(page.index("Connections need attention"), page.index("<h1>"))
        self.assertIn('href="/credentials#tmdb"', page)
        self.assertIn('href="/credentials#gmail"', page)
        self.assertIn("Fix Gmail", page)
        self.assertIn("Could not connect. Check your network and try again.", page)
        self.assertIn('href="/services/7/edit"', page)
        self.assertIn("No recent viewing.", page)
        self.assertNotIn("<small>To do</small>", page)
        self.client.application.config["CREDS"]["tmdb_api_key"] = "test-key"
        webapp._record_credential_test("email", True, "Gmail works.")
        with mock.patch.object(webapp, "run_analysis", return_value={"keep": [{"name": "Hulu", "reason": "Active viewing."}]}):
            page = self.client.get("/").data.decode()
        self.assertNotIn("Connections need attention", page)
        self.assertNotIn("0 changes", page)
        self.assertIn("All 1 service is earning their keep.", page)
        self.assertIn("Why each service is kept", page)


    def test_service_advice_and_kept_reason_copy(self):
        self._sign_in()
        self._post("/services", data={"name": "Hulu", "status": "subscribed", "monthly_cost": "10"})
        svc = webapp.get_all_services()[0]
        results = {"suspend": [{**svc, "reason": "No recent viewing."}],
                   "keep": [{"name": "Other", "reason": "1 active show(s). Prepaid through 2026-10-30."}]}
        with mock.patch.object(webapp, "run_analysis", return_value=results):
            page = self.client.get("/services").data
            self.assertIn(b'aria-label="Edit Hulu"', page)
            self.assertIn(b'class="pill yellow">Pause</span>', page)
            page = self.client.get(f"/services/{svc['id']}/edit").data
            self.assertIn(b"Pause", page)
            self.assertIn(b"No recent viewing.", page)
            page = self.client.get("/").data
            self.assertIn(f"1 active show. Prepaid through {webapp._nice_date('2026-10-30')}.", page.decode())
        self.assertEqual(webapp._recommendation_reason("2 active show(s)."), "2 active shows.")

    def test_page_titles_and_flash_roles(self):
        self._sign_in()
        for path, title in [("/", "Dashboard"), ("/services", "Services"),
                            ("/shows", "Shows"), ("/settings", "Settings")]:
            page = self.client.get(path).data
            self.assertIn(f"<title>{title} - Streamsaver</title>".encode(), page)
            self.assertIn(b'href="#main"', page)
        page = self._post("/lock", follow_redirects=True).data
        self.assertIn(b'Signed out.', page)
        self.assertIn(b'role="status"', page)
        self.assertIn(b'class="flash-stack" tabindex="-1"', page)
        self.assertNotIn(b'aria-live', page)
        page = self._post("/unlock", data={"passphrase": "wrong"}).data
        self.assertIn(b'role="alert"', page)
        self.assertIn(b'Authentication rejected.', page)

    def test_legacy_credential_failures_are_human_messages(self):
        self._sign_in()
        webapp._record_credential_test("email", False, "SMTP failure: private internal details")
        result = webapp._last_credential_test("email")
        self.assertEqual(result["message"], "Connection test failed. Try again.")
        page = self.client.get("/credentials").data
        self.assertNotIn(b"private internal details", page)

    def test_credential_failure_kinds(self):
        import smtplib
        import requests
        from cryptography.fernet import InvalidToken
        disconnected = RuntimeError("private")
        disconnected.__cause__ = smtplib.SMTPServerDisconnected("private")
        cases = [(disconnected, "Could not connect. Check your network and try again."),
                 (smtplib.SMTPConnectError(421, b"private"), "Could not connect. Check your network and try again."),
                 (webapp.TraktAuthError("private"), "Authentication rejected."),
                 (InvalidToken(), "Authentication rejected."),
                 (smtplib.SMTPAuthenticationError(535, b"private"), "Authentication rejected."),
                 (requests.HTTPError(response=mock.Mock(status_code=401)), "Authentication rejected."),
                 (requests.Timeout("private"), "Could not connect. Check your network and try again."),
                 (FileNotFoundError("private"), "Not configured. Add the connection details."),
                 (RuntimeError("private"), "Connection test failed. Try again."),
                 (RuntimeError("TMDb request failed (401)."), "Authentication rejected."),
                 (RuntimeError("TMDb request failed (Timeout)."), "Could not connect. Check your network and try again.")]
        for exc, message in cases:
            with self.subTest(kind=type(exc).__name__):
                self.assertEqual(webapp._credential_error_message(exc), message)
        self._sign_in()
        with mock.patch.object(webapp, "check_login", side_effect=smtplib.SMTPAuthenticationError(535, b"private")):
            page = self._post("/credentials/test/email", follow_redirects=True).data
        self.assertEqual(webapp._last_credential_test("email")["message"], "Authentication rejected.")
        self.assertIn(b'<p class="muted connection-message">Authentication rejected.</p>', page)
        self.assertNotIn(b"private", page)
        with mock.patch.object(webapp, "_tmdb_client") as client:
            page = self._post("/credentials/test/tmdb", follow_redirects=True).data
            client.assert_not_called()
        self.assertIn(b"Not configured. Add the connection details.", page)
        self.assertEqual(self._post("/credentials/test/unknown").status_code, 404)

    def test_service_mark_and_channel_display_helpers(self):
        self.assertEqual(webapp._service_mark("  Prime Video Channel ")["initials"], "PV")
        self.assertEqual(webapp._service_mark("hulu")["initials"], "HU")
        self.assertEqual(webapp._service_mark("")["initials"], "")
        self.assertEqual(webapp._service_mark("Prime Video"), webapp._service_mark("Prime Video"))
        self.assertIn(webapp._service_mark("Prime Video")["accent"],
                      ("purple", "pink", "cyan", "green", "orange", "yellow", "red"))
        self.assertEqual(webapp._service_name("Max Amazon Channel"), {"base": "Max", "channel": True})
        self.assertEqual(webapp._service_name("Amazon Channel News"), {"base": "Amazon Channel News", "channel": False})

    def test_spend_hero_marks_and_snoozed_edit(self):
        from core.service_manager import add_service
        self._sign_in()
        service_id = add_service("Prime Video Amazon Channel", status="subscribed", monthly_cost=12,
                                 billing_anchor_date=date.today().isoformat())
        with db.db_session() as conn:
            conn.execute("UPDATE services SET icon_path = ? WHERE id = ?", ("assets/logos/8.jpg", service_id))
        page = self.client.get("/").data.decode()
        self.assertIn('class="spend-amount">$12.00', page)
        self.assertIn("lands in the next 30 days", page)
        self.assertIn('src="/logos/8.jpg"', page)
        self.assertIn('width="32" height="32" alt="" loading="lazy"', page)
        self.assertIn('onerror="this.remove()"', page)
        self.assertIn('aria-hidden="true">PV</span>', page)
        page = self.client.get("/services").data.decode()
        self.assertIn('class="card-link"', page)
        self.assertIn('>Prime Video <span class="service-channel">Amazon Channel</span></a>', page)
        self.assertIn('class="service-channel">Amazon Channel', page)
        self.assertIn('aria-label="Edit Prime Video Amazon Channel"', page)
        self._post("/todo/snooze", data={"name": "Prime Video Amazon Channel", "action": "suspend"})
        page = self.client.get(f"/services/{service_id}/edit").data.decode()
        self.assertIn("Snoozed until", page)
        self.assertIn('aria-label="Undo snooze: Prime Video Amazon Channel"', page)
        with mock.patch.object(webapp, "run_analysis", return_value={"untracked": [{"name": "Untracked TV", "shows": ["A show"]}]}):
            page = self.client.get("/").data.decode()
        self.assertIn('aria-hidden="true">UT</span>', page)
        self.assertNotIn('src="/logos/8.jpg"', page)
        self.assertNotIn("Could save", page)

    def test_headline_distinguishes_monthly_and_actual_due_spend(self):
        from core.service_manager import add_service
        self._sign_in()
        add_service("Annual TV", status="subscribed", billing_cycle="annual",
                    annual_cost=120, billing_anchor_date=date.today().isoformat())
        rec = {**webapp.get_all_services()[0], "billing_date": date.today().isoformat(),
               "reason": "No recent viewing."}
        for results, savings in [({"suspend": [rec]}, True), ({"keep": [rec]}, False), ({}, False)]:
            with self.subTest(results=list(results)), mock.patch.object(webapp, "run_analysis", return_value=results):
                page = self.client.get("/").data.decode()
                self.assertEqual(page.count("<h1>"), 1)
                self.assertIn('class="spend-amount">$10.00</span> a month for 1 service.', page)
                self.assertNotIn('money-band', page)
                self.assertNotIn('class="metrics', page)
                self.assertEqual('class="todo-count"' in page, savings)
                self.assertLess(page.index('class="spend-headline"'), page.index('id="todo"'))
                if results:
                    self.assertIn('class="text-mono">$120.00</span>', page)
                if savings:
                    self.assertIn('Could save $10.00/mo.', page)
                else:
                    self.assertNotIn('Could save', page)
                    self.assertIn('Nothing to change.', page)
                    self.assertIn(f'All {1 if results else 0} service{"" if results else "s"} {"is" if results else "are"} earning their keep.', page)

    def test_keep_reason_footer_and_single_card_link(self):
        from core.service_manager import add_service
        self._sign_in()
        service_id = add_service("Paramount+ Amazon Channel", status="subscribed", monthly_cost=10)
        rec = {**webapp.get_all_services()[0], "reason": "1 active show(s). Viewer activity: High."}
        with mock.patch.object(webapp, "run_analysis", return_value={"keep": [rec]}):
            page = self.client.get('/services').data.decode()
            self.assertIn('class="keep-reason muted" title="1 active show. You watch it a lot."', page)
            self.assertEqual(page.count(f'href="/services/{service_id}/edit"'), 1)
            self.assertNotIn('card-edit', page)
            page = self.client.get('/').data.decode()
            kept = page[page.index('<ul class="kept">'):]
            self.assertIn('service-mark-24', kept)
            self.assertIn('<strong>Paramount+ <span class="service-channel">Amazon Channel</span></strong>', kept)
            page = self.client.get(f'/services/{service_id}/edit').data.decode()
            heading = page[page.index('<h1>'):page.index('</h1>')]
            self.assertNotIn('service-mark-24', heading)
            self.assertEqual(heading, "<h1>Paramount+ Amazon Channel")
        rec.update(action="suspend", until=date.today().isoformat())
        with mock.patch.object(webapp, "run_analysis", return_value={"snoozed": [rec]}):
            page = self.client.get(f'/services/{service_id}/edit').data.decode()
            self.assertIn('1 active show. You watch it a lot.', page)
            self.assertIn('>Undo snooze</button>', page)

    def test_trakt_button_tier(self):
        self._sign_in()
        for saved, test_ok, secondary in [(True, True, True), (True, None, True),
                                          (True, False, False), (False, True, False)]:
            with self.subTest(saved=saved, test_ok=test_ok):
                self.client.application.config['CREDS']['trakt_access_token'] = 'test-token' if saved else ''
                with mock.patch.object(webapp, '_last_credential_test', return_value=(
                        None if test_ok is None else {'ok': test_ok, 'at': '', 'message': ''})):
                    page = self.client.get('/credentials').data.decode()
                self.assertIn('<button type="submit"' + (' class="ghost"' if secondary else '') + '>Get Trakt code</button>', page)

    def test_shows_hide_scores_and_stale_dates(self):
        from datetime import timedelta
        self._sign_in()
        today = date.today()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id, title, interest_score, next_air_date, last_watch_date) VALUES (?, ?, ?, ?, ?)", [
                ("past", "Past", 14, str(today - timedelta(days=1)), str(today - timedelta(days=2))),
                ("watched", "Watched", 28, str(today), str(today + timedelta(days=1))),
                ("future", "Future", 0, str(today + timedelta(days=2)), str(today)),
            ])
        page = self.client.get("/shows?view=all").data.decode()
        self.assertNotIn('data-label="Score"', page)
        self.assertNotIn('<meter', page)
        self.assertNotIn('value="score"', page)
        self.assertIn("What do the states mean?", page)
        self.assertEqual(page.count('data-label="Next">—</td>'), 2)
        self.assertEqual(page.count('class="mobile-next"'), 1)
        self.assertNotIn('class="mobile-next"> · —', page)
        self.assertIn(webapp._nice_date(str(today + timedelta(days=2))), page)
        page = self.client.get("/shows?view=all&q=missing").data.decode()
        self.assertIn("0–0 of 0", page)

    def test_shows_pagination_sort_and_filters(self):
        self._sign_in()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id, title, show_state) VALUES (?, ?, ?)",
                             [(str(i), f"Series {i:03}", "active") for i in range(106)])
            conn.execute("INSERT INTO shows (trakt_id, title, show_state) VALUES ('done', 'Done', 'completed')")
        url = "/shows?view=all&q=Series&state=Active&sort=title&dir=asc"
        page = self.client.get(url).data
        self.assertIn("1–50 of 106", page.decode())
        self.assertIn(b"Next page", page)
        self.assertIn(b"Series 049", page)
        self.assertNotIn(b"Series 050", page)
        self.assertIn(b'aria-sort="ascending"', page)
        self.assertIn(b'<span class="sort-arrow asc" aria-hidden="true"><svg', page)
        self.assertIn(b'name="order"', page)
        self.assertIn(b'value="title:asc" selected', page)
        self.assertIn(b'q=Series&amp;state=Active&amp;group=All&amp;sort=title&amp;dir=asc&amp;page=2', page)
        page = self.client.get(url + "&page=3").data
        self.assertIn("101–106 of 106", page.decode())
        self.assertIn(b"Previous page", page)
        self.assertIn(b"Series 100", page)
        self.assertIn(b"Series 105", page)
        self.assertNotIn(b"Series 099", page)
        self.assertNotIn(b'rel="next"', page)
        for value in ["bad", "-1", "0"]:
            self.assertIn(b"Series 000", self.client.get(url + "&page=" + value).data)
        page = self.client.get("/shows?view=all&state=Completed&sort=invalid&dir=invalid").data
        self.assertIn(b"Done", page)
        self.assertNotIn(b"Abandon Done", page)
        self.assertIn(b'aria-sort="descending"', page)
        self.assertEqual(webapp._show_sort("invalid", "invalid"), ("state", "desc"))


    def test_coming_up_default_and_dashboard_rail(self):
        from datetime import timedelta
        from core.service_manager import add_service
        self._sign_in()
        today = date.today()
        sid = add_service("Netflix", status="subscribed", monthly_cost=40.03,
                          billing_anchor_date=str(today + timedelta(days=5)))
        with db.db_session() as conn:
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, show_state, service_id, next_air_date) VALUES (?, ?, 'anticipated', ?, ?)",
                [(str(i), title, sid, str(today + timedelta(days=2)))
                 for i, title in enumerate(["Love is Blind", "Bake Off", "Third show"])])
        page = self.client.get("/shows").data.decode()
        self.assertIn('href="/shows" aria-current="page"', page)
        self.assertIn('class="figure">1</strong> bills worth paying', page)
        self.assertIn('class="figure">$40.03</strong>', page)
        self.assertIn("1 later bill", page)
        self.assertIn("3 shows return first", page)
        self.assertIn("Third show", page)
        self.assertNotIn("<details><summary>+", page)
        self.assertNotIn('class="table-wrap"', page)
        self.assertIn("Love is Blind", page)
        dashboard = self.client.get("/").data.decode()
        self.assertIn('class="rail"', dashboard)
        self.assertIn("See all coming up", dashboard)
        self.assertIn('id="todo"', dashboard)
        self.assertIn("/shows?view=all", dashboard)  # Shows library menu link
        with db.db_session() as conn:
            conn.execute("DELETE FROM shows")
        page = self.client.get("/shows").data.decode()
        self.assertIn('href="/#todo"', page)
        self.assertIn("Pause suggested", page)

    def test_empty_timeline_and_rail(self):
        from datetime import timedelta
        self._sign_in()
        page = self.client.get("/shows").data.decode()
        self.assertIn("Nothing billing or returning in the next 60 days.", page)
        self.assertIn('href="/shows?view=all"', page)
        self.assertNotIn("Next 14 days", self.client.get("/").data.decode())
        with db.db_session() as conn:
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, next_air_date) VALUES (?, ?, ?)",
                [(str(i), f"Return {i}", str(date.today() + timedelta(days=i))) for i in range(7)])
        page = self.client.get("/").data.decode()
        self.assertIn("No bills in the next 30 days.", page)

    def test_frame_navigation_and_library(self):
        self._sign_in()
        for path, current in (("/", "/"), ("/shows", "/shows"),
                              ("/shows?view=all", "/shows?view=all"),
                              ("/services", "/services"), ("/settings", "/settings")):
            with self.subTest(path=path):
                page = self.client.get(path).data.decode()
                self.assertIn(f'href="{current}" aria-current="page"', page)
                nav = page.split('<nav class="nav"', 1)[1].split('</nav>', 1)[0]
                for href, label in (("/", "Dashboard"), ("/shows", "Coming up"), ("/services", "Services")):
                    self.assertIn(f'href="{href}"', nav)
                    self.assertIn(f'<span>{label}</span>', nav)
                self.assertEqual(nav.count('aria-current="page"'), int(current in ("/", "/shows", "/services")))
                self.assertNotIn('class="view-tabs"', page)
                self.assertNotIn('nav-secondary', page)
        page = self.client.get("/shows?view=all&q=Silo&state=Active&sort=title&dir=asc&page=2&endpoint=bad").data.decode()
        self.assertIn('<h1>Shows library</h1>', page)
        self.assertIn('href="/shows?view=all&amp;q=Silo&amp;state=Active&amp;group=All&amp;sort=title&amp;dir=asc&amp;page=1"', page)
        self.assertNotIn("endpoint=bad", page)
        dashboard = self.client.get("/").data.decode()
        self.assertNotIn('<h2>Actions</h2>', dashboard)
        self.assertNotIn('<h2>Show states</h2>', dashboard)

    def test_frame_sync_and_sign_out_forms(self):
        from datetime import datetime, timedelta
        import re
        self._sign_in()
        for stamp in ("", datetime.now().astimezone().replace(hour=2, minute=0).isoformat(),
                      (datetime.now().astimezone() - timedelta(days=1)).isoformat()):
            with db.db_session() as conn:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_trakt_sync', ?)", (stamp,))
            # About has no route-specific settings, so this also exercises context injection.
            page = self.client.get("/about").data.decode()
            sync = re.search(r'<form class="sync-form".*?</form>', page, re.S).group()
            self.assertIn('method="post" action="/actions/sync"', sync)
            self.assertIn('name="csrf_token"', sync)
            self.assertIn('title="Sync now"', sync)
            if stamp:
                self.assertIn('Last synced ' + webapp._local_time(stamp), sync)
                self.assertIn(webapp._local_time(stamp, compact=True), sync)
                self.assertIn('Synced </span>', sync)
            else:
                self.assertIn('<span>Sync now</span>', sync)
            menu = re.search(r'<details class="menu">.*?</details>', page, re.S).group()
            self.assertIn('<summary aria-label="More" class="active"><svg', menu)
            for label in ("Shows library", "Settings", "Credentials", "About"):
                self.assertIn(label, menu)
            sign_out = re.search(r'<form.*?</form>', menu, re.S).group()
            self.assertIn('method="post" action="/lock"', sign_out)
            self.assertIn('name="csrf_token"', sign_out)
            self.assertIn('Sign out</button>', sign_out)

    def test_local_time_compact(self):
        from datetime import datetime, timedelta
        now = datetime.now().astimezone().replace(hour=2, minute=0)
        self.assertEqual(webapp._local_time(now.isoformat(), compact=True), "2:00 AM")
        yesterday = now - timedelta(days=1)
        self.assertEqual(webapp._local_time(yesterday.isoformat(), compact=True),
                         f"{yesterday:%b} {yesterday.day}")
        self.assertEqual(webapp._local_time("", compact=True), "never")
        self.assertEqual(webapp._local_time("invalid", compact=True), "invalid")

    def test_legacy_score_sort_maps_to_state_and_title(self):
        self._sign_in()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id, title, show_state) VALUES (?, ?, ?)",
                             [("z", "Zulu", "active"), ("a", "Alpha", "active"), ("d", "Dormant", "dormant")])
        page = self.client.get("/shows?view=all&sort=score").data.decode()
        self.assertLess(page.index('class="title">Alpha'), page.index('class="title">Zulu'))
        self.assertIn('value="state:desc" selected', page)

    def test_rail_bill_anchor_all_returns_and_accessible_fallback(self):
        from datetime import timedelta
        from core.service_manager import add_service
        self._sign_in()
        today = date.today()
        sid = add_service("Preview", status="subscribed", monthly_cost=19.99,
                          billing_anchor_date=str(today + timedelta(days=12)))
        with db.db_session() as conn:
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, show_state, service_id, next_air_date) VALUES (?, ?, 'anticipated', ?, ?)",
                [(str(i), f"Return {i}", sid, str(today + timedelta(days=i))) for i in range(8)])
        page = self.client.get("/").data.decode()
        self.assertIn('class="rail"', page)
        self.assertIn('8 shows return before this bill', page)
        # Eight returns on consecutive days share four numbered dots.
        self.assertEqual(page.count('class="return-dot counts"'), 4)
        self.assertEqual(page.count('class="cluster-count"'), 4)
        self.assertIn('aria-hidden="true" focusable="false"', page)
        self.assertIn('<ul class="rail-accessible">', page)
        self.assertIn('class="sr-only rail-focus-link" href="/shows#bill-', page)
        self.assertNotIn('<figcaption', page)
        self.assertIn('Return 7', page)
        self.assertIn('width="44" height="44"', page)
        self.assertNotIn('Elsewhere returns', page)
        anchor = f'bill-{sid}-{today + timedelta(days=12)}'
        self.assertIn(f'href="/shows#{anchor}"', page)
        self.assertLess(page.index('class="rail"'), page.index('id="todo"'))
        shows_page = self.client.get("/shows").data.decode()
        self.assertIn(f'id="{anchor}"', shows_page)
        self.assertNotIn("<summary>+2 more</summary>", shows_page)
        # The return today counts toward the first bill, so nothing lands in Elsewhere.
        self.assertNotIn("Elsewhere", shows_page)
        self.assertIn("Return 0", shows_page)
        self.assertIn("Return 7", shows_page)

    def test_dashboard_synthetic_rail_tones_escaping_and_post_csrf(self):
        from datetime import timedelta
        from html.parser import HTMLParser

        self._sign_in()
        today = date.today()
        services = [{"id": sid, "name": f"Sample {sid}", "status": "subscribed"}
                    for sid in (1, 2, 3)]
        kinds = ("returns", "nothing_new", "unknown")
        events = [dict(type="bill", date=str(today + timedelta(days=day)),
                       service_id=sid, service_name=f"Sample {sid}", amount=10,
                       icon_path="assets/logos/sample.png" if sid == 1 else None,
                       verdict={"kind": kind, "text": "Synthetic verdict"})
                  for sid, day, kind in zip((1, 2, 3), (7, 8, 20), kinds)]
        events.append(dict(type="return", date=str(today + timedelta(days=1)),
                           service_id=1, title="<Sample & show>", context="counts toward next bill"))
        events.append(dict(type="return", date=str(today + timedelta(days=2)),
                           service_id=9, title="Elsewhere sample", context="not subscribed"))
        results = {key: [{"id": sid, "name": f"Sample {sid}", "reason": "Sample reason"}]
                   for sid, key in enumerate(("suspend", "resubscribe", "renew_soon", "untracked"), 1)}
        with mock.patch.object(webapp, "get_all_services", return_value=services), \
                mock.patch.object(webapp, "get_cost_totals", return_value={"monthly_total": 30}), \
                mock.patch.object(webapp, "run_analysis", return_value=results), \
                mock.patch.object(webapp, "build_timeline", return_value=[{"events": events}]):
            page = self.client.get("/").data.decode()
        self.assertIn('class="spend-amount">$30.00</span> a month for 3 services.', page)
        self.assertIn('class="text-yellow">1 bill has nothing coming back first</strong>', page)
        for tone in ("green", "yellow", "quiet"):
            self.assertIn(f'class="bill rail-tone-{tone}"', page)
        for tone in ("yellow", "cyan", "green", "pink"):
            self.assertIn(f'class="todo-tone-{tone}"', page)
        self.assertIn('href="/logos/sample.png"', page)
        self.assertIn('class="rail-initials"', page)
        rail_markup = page[page.index('<figure class="rail"'):page.index('</figure>')]
        self.assertNotIn('Elsewhere sample', rail_markup)
        self.assertNotIn('return-dot elsewhere', rail_markup)
        self.assertIn('Returns before this bill: &lt;Sample &amp; show&gt;', rail_markup)
        self.assertIn('class="badge-dot"', rail_markup)
        self.assertIn('See all coming up', rail_markup)
        self.assertIn('&lt;Sample &amp; show&gt;', page)
        self.assertIn('class="todo-done"', page)
        self.assertIn('>Paused it</button>', page)
        self.assertNotIn('Synced', page[page.index('id="todo"'):])

        class PostForms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tokens = []
                self.current = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form":
                    self.current = 0 if attrs.get("method", "get").lower() == "post" else None
                elif tag == "input" and attrs.get("name") == "csrf_token" and self.current is not None:
                    self.current += 1

            def handle_endtag(self, tag):
                if tag == "form" and self.current is not None:
                    self.tokens.append(self.current)
                    self.current = None

        forms = PostForms()
        forms.feed(page)
        self.assertGreater(len(forms.tokens), 3)
        self.assertTrue(all(count == 1 for count in forms.tokens))

    def test_polish_dashboard_order_filters_and_story(self):
        from core.service_manager import add_service
        from datetime import timedelta
        self._sign_in()
        sid = add_service("Example", status="subscribed", monthly_cost=10,
                          billing_anchor_date=str(date.today() + timedelta(days=7)))
        with mock.patch.object(webapp, "_connection_issues", return_value=[]):
            page = self.client.get("/").data.decode()
        self.assertLess(page.index('id="todo"'), page.index('class="rail"'))
        self.assertIn('aria-label="To do, 1 item"', page)
        self.assertIn('href="#todo">1 thing to change.', page)
        self.assertIn('>Paused it</button>', page)
        with mock.patch.object(webapp, "run_analysis", return_value={}):
            page = self.client.get("/").data.decode()
        self.assertLess(page.index('class="rail"'), page.index('id="todo"'))
        self.assertNotIn('class="todo-count"', page)
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id,title,show_state,service_id,next_air_date) VALUES (?,?,?,?,?)",
                             [(state, state.title() + " example", state, sid, str(date.today() + timedelta(days=2)))
                              for state in ('active', 'occasional', 'anticipated', 'abandoned')])
        page = self.client.get('/shows?view=all&group=Watching&order=title:asc').data.decode()
        self.assertIn('Active example', page)
        self.assertIn('Occasional example', page)
        self.assertNotIn('Anticipated example', page)
        self.assertIn('<details class="row-menu">', page)
        self.assertIn('class="pill green">Active</span>', page)
        page = self.client.get('/shows?view=all&group=Done&state=Anticipated&order=next:asc').data.decode()
        self.assertIn('Anticipated example', page)
        self.assertNotIn('Abandoned example', page)
        page = self.client.get(f'/services/{sid}/edit').data.decode()
        self.assertIn('<section class="service-story">', page)
        self.assertIn('class="window"', page)
        self.assertIn('Anticipated example', page)
        self.assertIn('<h2>Details</h2>', page)

    def test_polish_snoozed_return_and_free_ledger(self):
        from core.service_manager import add_service
        from core.todo import snooze
        from datetime import timedelta
        self._sign_in()
        sid = add_service('Paramount+ Amazon Channel', status='unsubscribed', monthly_cost=10)
        with db.db_session() as conn:
            conn.execute("INSERT INTO shows (trakt_id,title,show_state,service_id,next_air_date) VALUES ('return','Example return','anticipated',?,?)",
                         (sid, str(date.today() + timedelta(days=2))))
        with db.db_session() as conn:
            conn.execute("UPDATE shows SET interest_score = 80 WHERE trakt_id = 'return'")
        until = str(date.today() + timedelta(days=29))
        snooze('Paramount+ Amazon Channel', 'resubscribe', until)
        for path in ('/shows', '/services'):
            page = self.client.get(path).data.decode()
            self.assertIn('Snoozed until ' + webapp._nice_date(until), page)
            self.assertNotIn('class="resubscribe-link"', page)
        add_service('Free annual', status='subscribed', billing_cycle='annual', annual_cost=0,
                    billing_anchor_date=str(date.today() + timedelta(days=50)))
        page = self.client.get('/services').data.decode()
        self.assertIn('<div class="ledger-detail"><span class="muted">Free</span>', page)
        self.assertNotIn('Annual plan prepaid through', page)
        page = self.client.get(f'/services/{sid}/edit').data.decode()
        self.assertIn('Not currently paying.', page)
        self.assertNotIn('Nothing returns before this bill', page)
        annual = add_service('Distant annual', status='subscribed', billing_cycle='annual', annual_cost=120,
                             billing_anchor_date=str(date.today() + timedelta(days=180)))
        page = self.client.get(f'/services/{annual}/edit').data.decode()
        self.assertIn('No bill in the next 60 days.', page)
        self.assertNotIn('Nothing returns before this bill', page)

    def test_polish_provider_sections_and_maintenance_forms(self):
        import re
        self._sign_in()
        page = self.client.get('/credentials').data.decode()
        for label in ('Trakt', 'TMDb', 'Gmail'):
            self.assertEqual(page.count(f'<h2>{label}</h2>'), 1)
            self.assertNotIn(f'class="summary-heading">{label}', page)
        self.assertEqual(page.count('class="provider-section"'), 3)
        page = self.client.get('/settings').data.decode()
        self.assertEqual(page.count('class="maintenance-row"'), 6)
        self.assertIn('Discord webhook URL <span class="muted">Discord alerts: off', page)
        for path in ('/', '/services', '/services/new', '/shows?view=all', '/settings', '/credentials', '/about'):
            page = self.client.get(path).data.decode()
            forms = re.findall(r'<form\b[^>]*method="post".*?</form>', page, re.S)
            self.assertTrue(forms)
            self.assertTrue(all(form.count('name="csrf_token"') == 1 for form in forms))

    def test_state_sort_orders_by_meaning(self):
        states = ["active", "anticipated", "occasional", "abandoned", "completed", "dormant", "ignored", "archived"]
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id, title, show_state) VALUES (?, ?, ?)",
                             [(state, state, state) for state in reversed(states)])
        rows, _ = webapp._shows("", "All")
        self.assertEqual([row["show_state"] for row in rows], states)
        rows, _ = webapp._shows("", "All", direction="asc")
        self.assertEqual([row["show_state"] for row in rows], list(reversed(states)))

    def test_connection_controls_have_distinct_names(self):
        self._sign_in()
        self.client.application.config["CREDS"]["tmdb_api_key"] = "synthetic-key"
        page = self.client.get("/credentials").data.decode()
        for provider in ("Trakt", "TMDb", "Gmail"):
            self.assertIn(f'aria-label="Test {provider}"', page)
            self.assertIn(f'aria-label="Edit {provider}"', page)
        self.assertEqual(page.count('>Change</a>'), 3)

    def test_period_copy_preserves_verdict_population(self):
        from datetime import timedelta
        from core.service_manager import add_service
        self._sign_in()
        day = date.today()
        anticipated = add_service("Today TV", status="subscribed", monthly_cost=10,
                                  billing_anchor_date=str(day + timedelta(days=6)))
        active = add_service("Active TV", status="subscribed", monthly_cost=10,
                             billing_anchor_date=str(day + timedelta(days=6)))
        with db.db_session() as conn:
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, service_id, next_air_date, show_state) VALUES (?, ?, ?, ?, ?)",
                [("today", "Today premiere", anticipated, str(day), "anticipated"),
                 ("active", "Active return", active, str(day + timedelta(days=2)), "active")])
        page = self.client.get("/shows").data.decode()
        self.assertNotIn("0 shows return first", page)
        self.assertIn("1 show returns first", page)
        self.assertIn("Other tracked shows return below", page)
        self.assertIn("Active return", page)
        self.assertIn("Today premiere", page)

    def test_period_and_services_ledger_rendering(self):
        from datetime import timedelta
        from core.service_manager import add_service
        self._sign_in()
        day = date.today()
        paying = add_service("Paying Example", status="subscribed", monthly_cost=12,
                             billing_anchor_date=str(day + timedelta(days=6)))
        off = add_service("Off Example", status="suspended", monthly_cost=8)
        with db.db_session() as conn:
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, service_id, next_air_date, show_state) VALUES (?, ?, ?, ?, 'anticipated')",
                [("p", "Paying return", paying, str(day + timedelta(days=3))),
                 ("o", "Off return", off, str(day + timedelta(days=2)))])
        page = self.client.get("/shows").data.decode()
        self.assertIn(f'<article id="bill-{paying}-{day + timedelta(days=6)}"', page)
        for text in ('bills worth paying', 'shows return first', 'Elsewhere', 'window-fill'):
            self.assertIn(text if text != 'shows return first' else 'show returns first', page)
        page = self.client.get("/services").data.decode()
        for text in ('Paying</h2>', 'Off</h2>', 'ledger-row', 'Off return returns', 'Paused ·'):
            self.assertIn(text, page)
        self.assertNotIn('class="metrics"', page)
        self.assertNotIn('class="card ', page)
        dashboard = self.client.get("/").data.decode()
        self.assertIn('class="dashboard-top"', dashboard)
        self.assertNotIn('class="panel todo-panel"', dashboard)

    def test_return_resubscribe_links_and_inline_channel_name(self):
        from datetime import timedelta
        self._sign_in()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO services (id, name, status) VALUES (?, ?, ?)", [
                (1, "Paramount+ Amazon Channel", "unsubscribed"),
                (2, "Other", "not_subscribed")])
            conn.executemany(
                "INSERT INTO shows (trakt_id, title, service_id, next_air_date) VALUES (?, ?, ?, ?)",
                [(str(i), f"Return {i}", i, str(date.today() + timedelta(days=1))) for i in (1, 2)])
        with mock.patch.object(webapp, "run_analysis", return_value={"resubscribe": [{"id": 1}]}):
            page = self.client.get("/shows").data.decode()
        self.assertIn('href="/#todo">Resubscribe</a>', page)
        self.assertIn('href="/services/2/edit">Resubscribe</a>', page)
        self.assertIn('Paramount+ <span class="service-channel">Amazon Channel</span>, Cancelled', page)
        self.assertEqual(page.count('class="resubscribe-link"'), 2)

    def test_compact_library_filters_and_default_date_sort(self):
        import re
        self._sign_in()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id,title,show_state,next_air_date) VALUES (?,?,?,?)",
                             [('none', 'Undated example', 'abandoned', None),
                              ('late', 'Later example', 'anticipated', '2027-01-02'),
                              ('soon', 'Soon example', 'anticipated', '2027-01-01')])
        page = self.client.get('/shows?view=all').data.decode()
        toolbar = re.search(r'<form class="toolbar".*?</form>', page, re.S).group()
        self.assertEqual(toolbar.count('<option '), 4)
        self.assertNotIn('<select name="state"', toolbar)
        self.assertIn('value="next:asc" selected', toolbar)
        self.assertLess(page.index('class="title">Soon example'), page.index('class="title">Later example'))
        self.assertLess(page.index('class="title">Later example'), page.index('class="title">Undated example'))
        self.assertIn('class="pill quiet">Abandoned', page)
        page = self.client.get('/shows?view=all&state=Anticipated&q=example&order=title:asc').data.decode()
        self.assertIn('State: Anticipated', page)
        self.assertIn('aria-label="Clear state filter: Anticipated"', page)
        chip = re.search(r'<a class="state-chip.*?</a>', page, re.S).group()
        self.assertNotIn('state=', chip)
        self.assertIn('q=example', chip)
        self.assertIn('m6 6 12 12M18 6 6 18', chip)
        self.assertNotIn('Undated example', page)
        for order in ['service:asc', 'provider:desc', 'last:asc', 'next:desc']:
            page = self.client.get('/shows?view=all&order=' + order).data.decode()
            self.assertIn('class="title">Soon example', page)

    def test_shared_service_labels_free_count_and_name_only_heading(self):
        from core.service_manager import add_service
        self._sign_in()
        for status, label in [('subscribed', 'Paying'), ('suspended', 'Paused'),
                              ('unsubscribed', 'Cancelled'), ('not_subscribed', 'Never had')]:
            sid = add_service('Example ' + status, status=status, monthly_cost=10)
            page = self.client.get(f'/services/{sid}/edit').data.decode()
            self.assertIn(f'<h1>Example {status}</h1>', page)
            self.assertIn(f'value="{status}" selected>{label}</option>', page)
            self.assertIn(f'<span class="pill quiet">{label}</span>', page)
            self.assertIn('<details class="danger-section">', page)
            self.assertIn("return confirm('Delete", page)
        add_service('Free example', status='subscribed', monthly_cost=0)
        page = self.client.get('/services').data.decode()
        self.assertIn('1</span> paying · 1 free', page)
        for label in ['Paused', 'Cancelled', 'Never had']:
            self.assertIn(label + ' ·', page)

    def test_provider_purpose_and_visible_unique_summaries(self):
        self._sign_in()
        page = self.client.get('/credentials').data.decode()
        for label, purpose in [('Trakt', 'Viewing history and upcoming episodes'),
                               ('TMDb', 'Matching shows to streaming services'), ('Gmail', 'Sends your reports')]:
            self.assertIn(f'<summary>{label} connection details</summary>', page)
            self.assertIn(purpose, page)
        self.assertNotIn('<summary class="sr-only"', page)
        page = self.client.get('/settings').data.decode()
        self.assertIn('(sent only when there are service recommendations)', page)


if __name__ == "__main__":
    unittest.main()
