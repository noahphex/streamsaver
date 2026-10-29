import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import db
import setup_credentials as sc
import web.app as webapp


class SessionKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = Path(self.tmp.name) / '.secret_key'
        patch = mock.patch.object(webapp, 'CRED_FILE', str(Path(self.tmp.name) / 'credentials.enc'))
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_generates_and_reuses_private_file(self):
        first = webapp._session_secret()
        self.assertGreaterEqual(len(first), 64)
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(webapp._session_secret(), first)

    def test_env_overrides_file(self):
        os.environ['STREAMSAVER_SECRET_KEY'] = 'explicit-test-key'
        self.assertEqual(webapp._session_secret(), 'explicit-test-key')
        self.assertFalse(self.key.exists())
        del os.environ['STREAMSAVER_SECRET_KEY']
        generated = webapp._session_secret()
        os.environ['STREAMSAVER_SECRET_KEY'] = 'another-explicit-key'
        self.assertEqual(webapp._session_secret(), 'another-explicit-key')
        del os.environ['STREAMSAVER_SECRET_KEY']
        self.assertEqual(webapp._session_secret(), generated)

    def test_blank_and_known_defaults_use_file(self):
        for value in ('', 'streamsaver-local-change-me', 'change-me-local-streamsaver'):
            with self.subTest(value=value):
                os.environ['STREAMSAVER_SECRET_KEY'] = value
                self.assertNotEqual(webapp._session_secret(), value)
                self.assertTrue(self.key.exists())

    def test_concurrent_startup_reuses_one_key(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            keys = list(pool.map(lambda _: webapp._session_secret(), range(16)))
        self.assertEqual(len(set(keys)), 1)

    def test_old_defaults_cannot_forge_signed_in_session(self):
        from flask import Flask
        with mock.patch.object(db, 'DB_PATH', str(Path(self.tmp.name) / 'test.db')):
            client = webapp.create_app().test_client()
            for default in ('streamsaver-local-change-me', 'change-me-local-streamsaver'):
                attacker = Flask(__name__)
                attacker.secret_key = default
                cookie = attacker.session_interface.get_signing_serializer(attacker).dumps({'signed_in': True})
                client.set_cookie('session', cookie)
                response = client.get('/')
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers['Location'].endswith('/unlock'))



class DemoTests(unittest.TestCase):
    def test_seed_is_offline_analyzable_and_protects_existing_data(self):
        from scripts.seed_demo import seed_demo
        from core.analysis import run_analysis
        from core.rail import build_rail
        from core.timeline import build_timeline, group_periods
        from email_reports.logo import get_service_logo_cid
        with tempfile.TemporaryDirectory() as tmp:
            data, config = Path(tmp) / 'data', Path(tmp) / 'config'
            with mock.patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
                seed_demo(data, config)
                with mock.patch.object(db, 'DB_PATH', str(data / 'streamsaver.db')):
                    results = run_analysis()
                    self.assertTrue(results['suspend'])
                    self.assertTrue(results['resubscribe'])
                    timeline = build_timeline(results)
                    grouped = group_periods(timeline)
                    self.assertTrue(grouped['periods'])
                    self.assertTrue(grouped['off_returns'])
                    self.assertTrue(build_rail(timeline)['bills'])
                    with db.db_session() as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM shows').fetchone()[0], 25)
                        self.assertTrue(conn.execute("SELECT value FROM settings WHERE key='last_trakt_sync'").fetchone()[0])
                    with ExitStack() as stack:
                        stack.enter_context(mock.patch.object(webapp, 'CRED_FILE', str(config / 'credentials.enc')))
                        stack.enter_context(mock.patch.dict(os.environ, {'STREAMSAVER_PASSPHRASE': 'demo'}))
                        stack.enter_context(mock.patch.object(sc, 'CONFIG_DIR', str(config)))
                        stack.enter_context(mock.patch.object(sc, 'CRED_FILE', str(config / 'credentials.enc')))
                        stack.enter_context(mock.patch.object(sc, 'SALT_FILE', str(config / '.salt')))
                        client = webapp.create_app().test_client()
                        with client.session_transaction() as session:
                            session['signed_in'] = True
                        for route in ('/', '/shows', '/services'):
                            self.assertEqual(client.get(route).status_code, 200, route)
                for icon in (None, 'assets/logos/999999999.jpg'):
                    html, cid, attachment = get_service_logo_cid({'name': 'Demo', 'icon_path': icon})
                    self.assertTrue(html)
                    self.assertIsNone(cid)
                    self.assertIsNone(attachment)
            before = (data / 'streamsaver.db').read_bytes()
            with self.assertRaises(FileExistsError):
                seed_demo(data, config)
            self.assertEqual((data / 'streamsaver.db').read_bytes(), before)
            (data / 'streamsaver.db').unlink()
            with self.assertRaises(FileExistsError):
                seed_demo(data, config)
            seed_demo(data, config, force=True)

    def test_email_is_optional_but_partial_email_is_rejected(self):
        form = {'trakt_client_id': 'demo-client', 'trakt_client_secret': 'demo-secret', 'trakt_username': 'demo'}
        self.assertEqual(webapp._setup_credentials_from_form(form)['gmail_sender'], '')
        with self.assertRaises(RuntimeError):
            webapp._setup_credentials_from_form({**form, 'gmail_sender': 'demo@example.com'})

    def test_partial_email_does_not_consume_trakt_pin(self):
        form = {'trakt_client_id': 'demo-client', 'trakt_client_secret': 'demo-secret',
                'trakt_username': 'demo', 'trakt_pin': 'one-time-pin', 'gmail_sender': 'demo@example.com'}
        with mock.patch.object(webapp, '_exchange_trakt_pin') as exchange:
            with self.assertRaises(RuntimeError):
                webapp._setup_credentials_from_form(form)
        exchange.assert_not_called()
