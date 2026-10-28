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
                                "gmail_app_password": "keep-me"}, PASSPHRASE, quiet=True)
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
        self.assertIn(b"Email test failed: Gmail login failed", r.data)

    def test_todo_items_and_dates(self):
        items = webapp._todo_items({
            "resubscribe": [{"name": "Paramount+", "billing_date": "",
                             "shows": [{"title": "Cupertino", "next_air_date": "2026-10-30"}]}],
            "untracked": [{"name": "Crunchyroll", "shows": ["Frieren"]}],
            "keep": [{"name": "Hulu", "reason": "x"}],
        })
        self.assertEqual([(i["label"], i["name"]) for i in items],
                         [("Resubscribe", "Paramount+"), ("Add service", "Crunchyroll")])
        self.assertIn("Cupertino (Oct 30", items[0]["why"])
        self.assertEqual(webapp._money(1303.91), "$1,303.91")
        self.assertEqual(webapp._local_time(""), "never")


if __name__ == "__main__":
    unittest.main()
