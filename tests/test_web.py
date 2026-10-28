"""
tests/test_web.py
Streamsaver — web sign-in gate, cross-site POST block, and Trakt device login.

Run:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
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

    def _sign_in(self):
        r = self.client.post("/unlock", data={"passphrase": PASSPHRASE})
        self.assertEqual(r.status_code, 302)

    def test_pages_need_sign_in_but_health_does_not(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        r = self.client.get("/settings")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/unlock"))
        self.assertEqual(self.client.post("/settings", data={"notification_email": "x@evil.test"}).status_code, 403)

        self.client.post("/unlock", data={"passphrase": "wrong-passphrase"})
        self.assertEqual(self.client.get("/settings").status_code, 302)  # wrong passphrase: still out

        self._sign_in()
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.client.post("/lock")
        self.assertEqual(self.client.get("/settings").status_code, 302)

    def test_cross_site_post_is_rejected_even_when_signed_in(self):
        self._sign_in()
        r = self.client.post("/settings", data={"notification_email": "x@evil.test"},
                             headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/settings", data={"notification_email": "me@example.com"},
                             headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(webapp._get_setting("notification_email"), "me@example.com")

    def test_only_discord_webhooks_are_accepted(self):
        self._sign_in()
        self.client.post("/settings", data={"discord_webhook_url": "http://192.168.1.1/admin"})
        self.assertEqual(webapp._get_setting("discord_webhook_url"), "")
        self.client.post("/settings", data={"discord_webhook_url": "https://discord.com/api/webhooks/1/abc"})
        self.assertEqual(webapp._get_setting("discord_webhook_url"), "https://discord.com/api/webhooks/1/abc")

    def test_device_login_saves_new_tokens_and_keeps_other_credentials(self):
        self._sign_in()
        code = {"device_code": "dev", "user_code": "ABCD1234",
                "verification_url": "https://trakt.tv/activate", "expires_in": 600}
        with mock.patch.object(webapp, "request_device_code", return_value=code):
            self.client.post("/credentials/trakt/device/start")
        self.assertIn(b"ABCD1234", self.client.get("/credentials").data)

        with mock.patch.object(webapp, "poll_device_token", return_value=None):
            r = self.client.post("/credentials/trakt/device/finish", follow_redirects=True)
        self.assertIn(b"Not approved yet", r.data)

        with mock.patch.object(webapp, "poll_device_token",
                               return_value={"access_token": "new", "refresh_token": "new-r"}):
            self.client.post("/credentials/trakt/device/finish")
        saved = sc.load_credentials(PASSPHRASE)
        self.assertEqual((saved["trakt_access_token"], saved["trakt_refresh_token"]), ("new", "new-r"))
        self.assertEqual(saved["gmail_app_password"], "keep-me")


    def test_saving_trakt_details_without_pin_keeps_tokens(self):
        self._sign_in()
        self.client.post("/credentials/trakt", data={"trakt_client_id": "cid2", "trakt_username": "noah"})
        saved = sc.load_credentials(PASSPHRASE)
        self.assertEqual((saved["trakt_client_id"], saved["trakt_access_token"]), ("cid2", "old"))

    def test_email_form_without_report_field_keeps_report_address(self):
        self._sign_in()
        webapp._set_setting("notification_email", "reports@example.com")
        self.client.post("/credentials/email", data={"gmail_sender": "sender@gmail.com"})
        self.assertEqual(webapp._get_setting("notification_email"), "reports@example.com")

    def test_gmail_test_failure_is_reported_not_raised(self):
        self._sign_in()
        with mock.patch.object(webapp, "check_login", side_effect=RuntimeError("Gmail login failed (closed).")):
            r = self.client.post("/credentials/test/email", follow_redirects=True)
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
        self.client.post("/shows/t1/state", data={"state": "abandoned"})
        self.client.post("/shows/t1/state", data={"state": "abandoned"})  # double-click keeps the original
        self.client.post("/shows/t1/state", data={"state": "restore"})
        with db.db_session() as conn:
            row = conn.execute("SELECT show_state, abandoned_override FROM shows WHERE trakt_id = 't1'").fetchone()
        self.assertEqual((row["show_state"], row["abandoned_override"]), ("anticipated", 0))

    def test_credential_test_result_is_remembered(self):
        self._sign_in()
        with mock.patch.object(webapp, "check_login", return_value=None):
            self.client.post("/credentials/test/email")
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
        self.assertEqual(webapp._todo_summary({}), "0 changes")
        self.assertEqual(webapp._todo_summary({"resubscribe": [{"monthly_cost": 20}]}), "1 change")
        results = {
            "suspend": [{"monthly_cost": 12.50}, {"monthly_cost": None}],
            "consider_non_renewal": [{"billing_cycle": "annual", "annual_cost": 120, "monthly_cost": 999}],
            "renew_soon": [{"billing_cycle": "annual", "annual_cost": 240}],
            "resubscribe": [{"monthly_cost": 30}],
            "keep": [{"monthly_cost": 50}],
        }
        self.assertEqual(webapp._todo_summary(results), "5 changes · could save $22.50/mo")
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
        self.assertIn('<h1>To do</h1>', page)
        self.assertNotIn("Streaming Cost Control", page)
        self.assertIn("1 change · could save $15.00/mo", page)
        self.assertIn("Synced never", page)
        self.assertIn('role="status" aria-label="Connections need attention"', page)
        self.assertLess(page.index("Connections need attention"), page.index("<h1>To do</h1>"))
        self.assertIn('href="/credentials#tmdb"', page)
        self.assertIn('href="/credentials#gmail"', page)
        self.assertIn("Fix Gmail →", page)
        self.assertIn("Could not connect. Check your network and try again.", page)
        self.assertIn('href="/services/7/edit"', page)
        self.assertIn("No recent viewing.", page)
        self.assertNotIn("<small>To do</small>", page)
        self.client.application.config["CREDS"]["tmdb_api_key"] = "test-key"
        webapp._record_credential_test("email", True, "Gmail works.")
        with mock.patch.object(webapp, "run_analysis", return_value={"keep": [{"name": "Hulu", "reason": "Active viewing."}]}):
            page = self.client.get("/").data.decode()
        self.assertNotIn("Connections need attention", page)
        self.assertIn("0 changes", page)
        self.assertIn("Nothing to change.</strong> 1 service worth keeping right now.", page)
        self.assertIn("Why each service is kept", page)


    def test_service_advice_and_kept_reason_copy(self):
        self._sign_in()
        self.client.post("/services", data={"name": "Hulu", "status": "subscribed", "monthly_cost": "10"})
        svc = webapp.get_all_services()[0]
        results = {"suspend": [{**svc, "reason": "No recent viewing."}],
                   "keep": [{"name": "Other", "reason": "1 active show(s). Prepaid through 2026-10-30."}]}
        with mock.patch.object(webapp, "run_analysis", return_value=results):
            page = self.client.get("/services").data
            self.assertIn(b'aria-label="Edit Hulu"', page)
            self.assertIn(b'<p class="muted">No recent viewing.</p>', page)
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
        page = self.client.post("/lock", follow_redirects=True).data
        self.assertIn(b'Signed out.', page)
        self.assertIn(b'role="status"', page)
        self.assertIn(b'class="flash-stack" tabindex="-1"', page)
        self.assertNotIn(b'aria-live', page)
        page = self.client.post("/unlock", data={"passphrase": "wrong"}).data
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
            page = self.client.post("/credentials/test/email", follow_redirects=True).data
        self.assertEqual(webapp._last_credential_test("email")["message"], "Authentication rejected.")
        self.assertIn(b'<p class="muted connection-message">Authentication rejected.</p>', page)
        self.assertNotIn(b"private", page)
        with mock.patch.object(webapp, "_tmdb_client") as client:
            page = self.client.post("/credentials/test/tmdb", follow_redirects=True).data
            client.assert_not_called()
        self.assertIn(b"Not configured. Add the connection details.", page)
        self.assertEqual(self.client.post("/credentials/test/unknown").status_code, 404)

    def test_shows_pagination_sort_and_filters(self):
        self._sign_in()
        with db.db_session() as conn:
            conn.executemany("INSERT INTO shows (trakt_id, title, show_state) VALUES (?, ?, ?)",
                             [(str(i), f"Series {i:03}", "active") for i in range(106)])
            conn.execute("INSERT INTO shows (trakt_id, title, show_state) VALUES ('done', 'Done', 'completed')")
        url = "/shows?q=Series&state=Active&sort=title&dir=asc"
        page = self.client.get(url).data
        self.assertIn(b"Series 049", page)
        self.assertNotIn(b"Series 050", page)
        self.assertIn(b'aria-sort="ascending"', page)
        self.assertIn(b'<span class="sort-arrow" aria-hidden="true">', page)
        self.assertIn(b'name="sort"', page)
        self.assertIn(b'name="dir"', page)
        self.assertIn(b'q=Series&amp;state=Active&amp;sort=title&amp;dir=asc&amp;page=2', page)
        page = self.client.get(url + "&page=3").data
        self.assertIn(b"Series 100", page)
        self.assertIn(b"Series 105", page)
        self.assertNotIn(b"Series 099", page)
        self.assertNotIn(b'rel="next"', page)
        for value in ["bad", "-1", "0"]:
            self.assertIn(b"Series 000", self.client.get(url + "&page=" + value).data)
        page = self.client.get("/shows?state=Completed&sort=invalid&dir=invalid").data
        self.assertIn(b"Done", page)
        self.assertNotIn(b"Abandon Done", page)
        self.assertIn(b'aria-sort="descending"', page)
        self.assertEqual(webapp._show_sort("invalid", "invalid"), ("score", "desc"))


if __name__ == "__main__":
    unittest.main()
