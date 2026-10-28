import os
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import (
    abort,
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    send_file,
    session,
    url_for,
)

from core.analysis import run_analysis
from core.backup import export_backup, import_backup
from core.newsletter import (
    FREQUENCY_OPTIONS,
    filter_status_alerts_for_frequency,
    has_status_alerts,
    mark_status_alerts_sent,
)
from core.notifications import send_discord_notification
from core.service_manager import (
    VALID_STATUSES,
    add_service,
    delete_service,
    fetch_and_cache_networks,
    get_all_services,
    get_bundle_providers,
    get_cached_network_names,
    get_cost_totals,
    get_network_provider_id,
    set_bundle_providers,
    update_service,
)
from core.show_tracker import backfill_service_ids, run_full_sync
from core.tmdb_client import TMDbClient
from core.trakt_client import TraktClient, poll_device_token, request_device_code
from db import db_session, init_db
from email_reports.mailer import send_report
from email_reports.report_builder import build_all_clear, build_report
from setup_credentials import (
    CRED_FILE,
    TRAKT_OAUTH_AUTHORIZE,
    TRAKT_OAUTH_TOKEN,
    TRAKT_REDIRECT_URI,
    encrypt_credentials,
    load_credentials,
    load_trakt_tokens,
    save_trakt_tokens,
)

logger = logging.getLogger(__name__)


_PUBLIC_ENDPOINTS = {"health", "unlock", "static", "logos"}
_DISCORD_WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")

SHOW_SORT_COLUMNS = {
    "title": "LOWER(s.title)",
    "state": "LOWER(s.show_state)",
    "score": "s.interest_score",
    "service": "LOWER(COALESCE(svc.name, ''))",
    "provider": "LOWER(COALESCE(s.primary_provider, ''))",
    "last": "COALESCE(s.last_watch_date, '')",
    "next": "COALESCE(s.next_air_date, '')",
}


def create_app():
    _setup_logging()
    init_db()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.environ.get("STREAMSAVER_SECRET_KEY", "streamsaver-local-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    # Browsers won't send the session on cross-site POSTs; a sign-in lasts 30 days per browser.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    app.config["CREDS"] = None
    app.config["CRED_PASSPHRASE"] = None

    @app.context_processor
    def inject_globals():
        return {
            "is_unlocked": bool(_credentials(optional=True)),
            "status_labels": {
                "subscribed": "Subscribed",
                "suspended": "Suspended",
                "unsubscribed": "Unsubscribed",
                "not_subscribed": "Not Subscribed",
            },
        }

    @app.before_request
    def require_sign_in():
        # The app is reachable from the whole LAN and auto-unlocks credentials from the env,
        # so every page needs a signed-in browser, and POSTs must come from this site.
        if request.method == "POST" and not _same_origin():
            abort(403)
        if session.get("signed_in") or request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        if request.endpoint == "setup" and not Path(CRED_FILE).exists():
            return None  # first run: nothing to protect yet
        if request.method == "POST":
            abort(403)
        return redirect(url_for("unlock"))

    def _same_origin():
        source = request.headers.get("Origin") or request.headers.get("Referer")
        return not source or urlsplit(source).netloc == request.host

    def _sign_in():
        session.permanent = True
        session["signed_in"] = True
        session["unlocked_at"] = datetime.now().isoformat(timespec="seconds")

    @app.get("/health")
    def health():
        return {"ok": True, "unlocked": bool(_credentials(optional=True))}

    @app.get("/logos/<path:filename>")
    def logos(filename):
        return send_from_directory(Path(__file__).resolve().parent.parent / "assets" / "logos", filename)

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        has_credentials = Path(CRED_FILE).exists()
        if has_credentials and not _credentials(optional=True):
            flash("Unlock Streamsaver before replacing existing credentials.", "error")
            return redirect(url_for("unlock"))
        if request.method == "POST":
            passphrase = request.form.get("passphrase", "")
            confirm_passphrase = request.form.get("confirm_passphrase", "")
            if len(passphrase) < 8:
                flash("Passphrase must be at least 8 characters.", "error")
                return redirect(url_for("setup"))
            if passphrase != confirm_passphrase:
                flash("Passphrases do not match.", "error")
                return redirect(url_for("setup"))
            try:
                creds = _setup_credentials_from_form(request.form)
                encrypt_credentials(creds, passphrase)
                app.config["CREDS"] = creds
                app.config["CRED_PASSPHRASE"] = passphrase
                _sign_in()
                if creds.get("notify_email"):
                    _set_setting("notification_email", creds["notify_email"])
                if not creds["trakt_access_token"]:
                    flash("Credentials saved. Now sign in to Trakt with a device code.", "success")
                    return redirect(url_for("credentials"))
                flash("Credentials saved and Streamsaver unlocked.", "success")
                return redirect(url_for("settings"))
            except Exception as exc:
                logger.exception("Web credential setup failed")
                flash(f"Setup failed: {exc}", "error")
        return render_template(
            "setup.html",
            has_credentials=has_credentials,
            trakt_auth_url=_trakt_auth_url(request.form.get("trakt_client_id", "")),
        )

    @app.route("/unlock", methods=["GET", "POST"])
    def unlock():
        if request.method == "POST":
            passphrase = request.form.get("passphrase", "").strip()
            try:
                app.config["CREDS"] = load_credentials(passphrase)
                app.config["CRED_PASSPHRASE"] = passphrase
                _sign_in()
                flash("Streamsaver unlocked.", "success")
                return redirect(url_for("dashboard"))
            except Exception as exc:
                flash(f"Could not unlock credentials: {exc}", "error")
        return render_template("unlock.html", has_credentials=Path(CRED_FILE).exists())

    @app.post("/lock")
    def lock():
        app.config["CREDS"] = None
        app.config["CRED_PASSPHRASE"] = None
        session.clear()
        flash("Credentials locked.", "info")
        return redirect(url_for("unlock"))

    @app.get("/")
    def dashboard():
        services = get_all_services()
        totals = get_cost_totals(status_filter="subscribed")
        counts = _show_counts()
        settings = _settings()
        recommendations = run_analysis()
        return render_template(
            "dashboard.html",
            services=services,
            totals=totals,
            counts=counts,
            settings=settings,
            recommendations={key: len(value) for key, value in recommendations.items()},
        )

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.get("/services")
    def services():
        all_services = get_all_services()
        bundle_map = {svc["id"]: get_bundle_providers(svc["id"]) for svc in all_services}
        return render_template(
            "services.html",
            services=all_services,
            bundle_map=bundle_map,
            totals=get_cost_totals(status_filter="subscribed"),
        )

    @app.get("/services/new")
    def service_new():
        return _service_form()

    @app.get("/services/<int:service_id>/edit")
    def service_edit(service_id):
        editable = next((svc for svc in get_all_services() if svc["id"] == service_id), None)
        if editable is None:
            flash("Service not found.", "error")
            return redirect(url_for("services"))
        return _service_form(editable)

    @app.post("/services")
    def service_save():
        form = request.form
        service_id = form.get("service_id")
        payload = _service_payload(form)
        bundles = _bundle_payload(form.get("bundle_provider_ids", ""))
        try:
            if service_id:
                update_service(int(service_id), **payload)
                set_bundle_providers(int(service_id), bundles)
                flash("Service updated.", "success")
            else:
                new_id = add_service(**payload)
                set_bundle_providers(new_id, bundles)
                flash("Service added.", "success")
            backfill_service_ids()  # providers/bundles changed: re-link shows now, not at the next sync
        except Exception as exc:
            flash(f"Could not save service: {exc}", "error")
            if service_id:
                return redirect(url_for("service_edit", service_id=service_id))
            return redirect(url_for("service_new"))
        return redirect(url_for("services"))

    @app.post("/services/<int:service_id>/delete")
    def delete_service_route(service_id):
        try:
            delete_service(service_id)
            backfill_service_ids()
            flash("Service deleted.", "success")
        except Exception as exc:
            flash(f"Could not delete service: {exc}", "error")
        return redirect(url_for("services"))

    @app.get("/shows")
    def shows():
        q = request.args.get("q", "").strip()
        state = request.args.get("state", "All")
        sort, direction = _show_sort(request.args.get("sort", "score"), request.args.get("dir", "desc"))
        rows = _shows(q, state, sort, direction)
        return render_template("shows.html", shows=rows, q=q, state=state, sort=sort, direction=direction)

    @app.post("/shows/<trakt_id>/state")
    def set_show_state(trakt_id):
        state = request.form.get("state", "dormant")
        if state not in {"abandoned", "dormant", "ignored"}:
            flash("Invalid show state.", "error")
            return redirect(url_for("shows"))
        abandoned_override = 1 if state == "abandoned" else 0
        with db_session() as conn:
            conn.execute(
                "UPDATE shows SET show_state = ?, abandoned_override = ? WHERE trakt_id = ?",
                (state, abandoned_override, trakt_id),
            )
        flash("Show updated.", "success")
        return redirect(request.referrer or url_for("shows"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            _set_setting("notification_email", request.form.get("notification_email", "").strip())
            _set_setting("provider_country", (request.form.get("provider_country", "US").strip().upper() or "US"))
            frequency = request.form.get("status_alert_frequency", "weekly")
            if frequency not in FREQUENCY_OPTIONS and frequency != "never":
                frequency = "weekly"
            _set_setting("status_alert_frequency", frequency)
            webhook_url = request.form.get("discord_webhook_url", "").strip()
            if request.form.get("clear_discord_webhook") == "1":
                _set_setting("discord_webhook_url", "")
            elif webhook_url:
                if not webhook_url.startswith(_DISCORD_WEBHOOK_PREFIXES):
                    flash("Discord webhook must start with https://discord.com/api/webhooks/.", "error")
                    return redirect(url_for("settings"))
                _set_setting("discord_webhook_url", webhook_url)
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        return render_template("settings.html", settings=_settings())

    @app.get("/credentials")
    def credentials():
        creds = _credentials()
        return render_template(
            "credentials.html",
            creds=_credential_view(creds),
            trakt_auth_url=_trakt_auth_url(creds.get("trakt_client_id", "")),
            trakt_device=_pending_trakt_device(),
        )

    @app.post("/credentials/trakt/device/start")
    def start_trakt_device_login():
        try:
            creds = _credentials()
            code = request_device_code(creds["trakt_client_id"])
            # Server-side only (single Gunicorn worker): the device code is a credential until it expires.
            app.config["TRAKT_DEVICE"] = {
                "device_code": code["device_code"],
                "user_code": code["user_code"],
                "verification_url": code["verification_url"],
                "expires_at": datetime.now().timestamp() + int(code["expires_in"]),
            }
        except Exception as exc:
            logger.exception("Trakt device login start failed")
            flash(f"Could not start Trakt device login: {exc}", "error")
        return redirect(url_for("credentials"))

    @app.post("/credentials/trakt/device/finish")
    def finish_trakt_device_login():
        pending = _pending_trakt_device()
        if not pending:
            flash("No Trakt device login in progress, or the code expired. Start again.", "error")
            return redirect(url_for("credentials"))
        try:
            creds = dict(_credentials())
            tokens = poll_device_token(creds["trakt_client_id"], creds.get("trakt_client_secret", ""),
                                       pending["device_code"])
            if tokens is None:
                flash(f"Not approved yet. Enter {pending['user_code']} at {pending['verification_url']}, then click Finish again.", "error")
                return redirect(url_for("credentials"))
            creds["trakt_access_token"] = tokens["access_token"]
            creds["trakt_refresh_token"] = tokens.get("refresh_token", "")
            _save_credentials(creds)
            app.config.pop("TRAKT_DEVICE", None)
            flash("Trakt authorization updated. The nightly job picks it up on its next run.", "success")
        except Exception as exc:
            app.config.pop("TRAKT_DEVICE", None)
            logger.exception("Trakt device login failed")
            flash(f"Trakt device login failed: {exc}", "error")
        return redirect(url_for("credentials"))

    def _pending_trakt_device():
        pending = app.config.get("TRAKT_DEVICE")
        if pending and pending["expires_at"] > datetime.now().timestamp():
            return pending
        app.config.pop("TRAKT_DEVICE", None)
        return None

    @app.post("/credentials/trakt")
    def update_trakt_credentials():
        try:
            creds = dict(_credentials())
            client_id = request.form.get("trakt_client_id", "").strip()
            client_secret = request.form.get("trakt_client_secret", "").strip() or creds.get("trakt_client_secret", "")
            username = request.form.get("trakt_username", "").strip()
            pin = request.form.get("trakt_pin", "").strip()
            missing = []
            if not client_id:
                missing.append("Client ID")
            if not client_secret:
                missing.append("Client Secret")
            if not username:
                missing.append("Trakt Username")
            if not pin:
                missing.append("PIN from Trakt")
            if missing:
                raise RuntimeError(f"Missing Trakt fields: {', '.join(missing)}.")

            token_data = _exchange_trakt_pin(client_id, client_secret, pin)
            creds.update(
                {
                    "trakt_client_id": client_id,
                    "trakt_client_secret": client_secret,
                    "trakt_access_token": token_data["access_token"],
                    "trakt_refresh_token": token_data.get("refresh_token", ""),
                    "trakt_username": username,
                }
            )
            _save_credentials(creds)
            flash("Trakt authorization updated.", "success")
        except Exception as exc:
            logger.exception("Trakt credential update failed")
            flash(f"Trakt update failed: {exc}", "error")
        return redirect(url_for("credentials"))

    @app.post("/credentials/tmdb")
    def update_tmdb_credentials():
        try:
            creds = dict(_credentials())
            api_key = request.form.get("tmdb_api_key", "").strip()
            if request.form.get("clear_tmdb_api_key") == "1":
                creds["tmdb_api_key"] = ""
                flash("TMDb API key removed.", "success")
            elif api_key:
                creds["tmdb_api_key"] = api_key
                flash("TMDb API key updated.", "success")
            else:
                flash("No TMDb changes saved.", "info")
                return redirect(url_for("credentials"))
            _save_credentials(creds)
        except Exception as exc:
            logger.exception("TMDb credential update failed")
            flash(f"TMDb update failed: {exc}", "error")
        return redirect(url_for("credentials"))

    @app.post("/credentials/email")
    def update_email_credentials():
        try:
            creds = dict(_credentials())
            sender = request.form.get("gmail_sender", "").strip()
            app_password = request.form.get("gmail_app_password", "").strip()
            notify_email = request.form.get("notify_email", "").strip()
            if not sender:
                raise RuntimeError("Gmail sender address is required.")
            creds["gmail_sender"] = sender
            if app_password:
                creds["gmail_app_password"] = app_password
            creds["notify_email"] = notify_email or sender
            _set_setting("notification_email", creds["notify_email"])
            _save_credentials(creds)
            flash("Email report credentials updated.", "success")
        except Exception as exc:
            logger.exception("Email credential update failed")
            flash(f"Email update failed: {exc}", "error")
        return redirect(url_for("credentials"))

    @app.post("/actions/sync")
    def action_sync():
        try:
            creds = _credentials()
            force_backfill = request.form.get("force_backfill") == "1"
            results = run_full_sync(
                _trakt_client(creds, _persist_trakt_tokens, _load_saved_trakt_tokens),
                _tmdb_client(creds),
                force_backfill=force_backfill,
            )
            logger.info("Manual Trakt sync completed: force_backfill=%s results=%s", force_backfill, results)
            mode = "Backfill complete" if force_backfill else "Sync complete"
            flash(
                f"{mode}: {results['shows']} shows, {results['watchlist']} watchlist, {results['archived']} archived.",
                "success",
            )
        except Exception as exc:
            logger.exception("Manual Trakt sync failed")
            flash(f"Sync failed: {exc}", "error")
        return redirect(request.referrer or url_for("dashboard"))

    @app.post("/actions/refresh-providers")
    def action_refresh_providers():
        try:
            creds = _credentials()
            client = _tmdb_client(creds)
            if client is None:
                raise RuntimeError("No TMDb API key configured.")
            count = fetch_and_cache_networks(client)
            flash(f"Provider list refreshed: {count} providers cached.", "success")
        except Exception as exc:
            flash(f"Provider refresh failed: {exc}", "error")
        return redirect(request.referrer or url_for("settings"))

    @app.post("/actions/analyze")
    def action_analyze():
        try:
            creds = _credentials()
            notify_email = _get_setting("notification_email") or creds.get("notify_email", "")
            if not notify_email:
                raise RuntimeError("No notification email configured.")
            sent = _send_analysis_reports(creds, notify_email)
            if sent:
                flash(f"Analysis complete. {sent} email(s) sent.", "success")
            else:
                # A manual run should always produce an email; the nightly job stays quiet instead.
                _send_all_clear(creds, notify_email)
                flash("Analysis complete: nothing to change. Sent an all-clear summary.", "success")
        except Exception as exc:
            flash(f"Analysis failed: {exc}", "error")
        return redirect(request.referrer or url_for("dashboard"))

    @app.post("/actions/test-discord")
    def action_test_discord():
        webhook_url = _get_setting("discord_webhook_url")
        if not webhook_url:
            flash("No Discord webhook configured.", "error")
            return redirect(request.referrer or url_for("settings"))
        ok = send_discord_notification(
            webhook_url,
            "Streamsaver Discord test",
            "Discord notifications are configured correctly.",
            level="success",
            footer="Triggered from Settings",
        )
        if ok:
            flash("Discord test notification sent.", "success")
        else:
            flash("Discord test notification failed. Check the webhook URL and logs.", "error")
        return redirect(request.referrer or url_for("settings"))

    @app.get("/backup/export")
    def backup_export():
        fd, path = tempfile.mkstemp(prefix="streamsaver_backup_", suffix=".json")
        os.close(fd)
        count = export_backup(path)
        return send_file(
            path,
            as_attachment=True,
            download_name=f"streamsaver_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mimetype="application/json",
        )

    @app.post("/backup/restore")
    def backup_restore():
        uploaded = request.files.get("backup")
        if not uploaded or not uploaded.filename:
            flash("Choose a backup file first.", "error")
            return redirect(url_for("settings"))
        fd, path = tempfile.mkstemp(prefix="streamsaver_restore_", suffix=".json")
        os.close(fd)
        uploaded.save(path)
        try:
            services_count, settings_count = import_backup(path)
            flash(f"Restore complete: {services_count} services, {settings_count} settings.", "success")
        except Exception as exc:
            flash(f"Restore failed: {exc}", "error")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return redirect(url_for("settings"))

    def _credentials(optional=False):
        if app.config.get("CREDS"):
            return app.config["CREDS"]
        passphrase = os.environ.get("STREAMSAVER_PASSPHRASE")
        if passphrase:
            try:
                app.config["CREDS"] = load_credentials(passphrase)
                app.config["CRED_PASSPHRASE"] = passphrase
                return app.config["CREDS"]
            except Exception:
                if not optional:
                    raise
        if optional:
            return None
        raise RuntimeError("Streamsaver is locked. Unlock credentials first.")

    def _persist_trakt_tokens(tokens):
        creds = app.config.get("CREDS")
        if not creds:
            logger.warning("Trakt token refreshed but credentials are not loaded; cannot persist token.")
            return
        creds["trakt_access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            creds["trakt_refresh_token"] = tokens["refresh_token"]
        app.config["CREDS"] = creds
        passphrase = _passphrase()
        if not passphrase:
            logger.warning("Trakt token refreshed but no passphrase is available; token was not persisted.")
            return
        # Merge into the file on disk, then adopt it, so newer saved changes win over this copy.
        app.config["CREDS"] = save_trakt_tokens(passphrase, tokens["access_token"], tokens.get("refresh_token", ""))
        logger.info("Persisted refreshed Trakt tokens.")

    def _load_saved_trakt_tokens():
        passphrase = _passphrase()
        return load_trakt_tokens(passphrase) if passphrase else {}

    def _passphrase():
        return app.config.get("CRED_PASSPHRASE") or os.environ.get("STREAMSAVER_PASSPHRASE")

    def _save_credentials(creds):
        passphrase = app.config.get("CRED_PASSPHRASE") or os.environ.get("STREAMSAVER_PASSPHRASE")
        if not passphrase:
            raise RuntimeError("Credentials are unlocked for this session, but the passphrase is unavailable. Lock and unlock again before saving credential changes.")
        encrypt_credentials(creds, passphrase, quiet=True)
        app.config["CREDS"] = creds

    return app


def _service_form(editable=None):
    bundle_map = {}
    if editable:
        bundle_map[editable["id"]] = get_bundle_providers(editable["id"])
    return render_template(
        "service_form.html",
        editable=editable,
        bundle_map=bundle_map,
        network_names=get_cached_network_names(),
        statuses=sorted(VALID_STATUSES),
    )


def _get_setting(key):
    with db_session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _set_setting(key, value):
    with db_session() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def _settings():
    keys = [
        "notification_email",
        "provider_country",
        "discord_webhook_url",
        "status_alert_frequency",
        "last_status_alert_sent",
        "last_trakt_sync",
        "networks_fetched_at",
    ]
    settings = {key: _get_setting(key) for key in keys}
    settings["discord_webhook_configured"] = bool(settings.get("discord_webhook_url"))
    return settings


def _credential_view(creds):
    return {
        "trakt_client_id": creds.get("trakt_client_id", ""),
        "trakt_username": creds.get("trakt_username", ""),
        "has_trakt_client_secret": bool(creds.get("trakt_client_secret")),
        "has_trakt_access_token": bool(creds.get("trakt_access_token")),
        "has_trakt_refresh_token": bool(creds.get("trakt_refresh_token")),
        "has_tmdb_api_key": bool(creds.get("tmdb_api_key")),
        "gmail_sender": creds.get("gmail_sender", ""),
        "has_gmail_app_password": bool(creds.get("gmail_app_password")),
        "notify_email": creds.get("notify_email", ""),
    }


def _trakt_auth_url(client_id):
    if not client_id:
        return ""
    return (
        f"{TRAKT_OAUTH_AUTHORIZE}"
        f"?response_type=code"
        f"&client_id={client_id.strip()}"
        f"&redirect_uri={TRAKT_REDIRECT_URI}"
    )


def _setup_credentials_from_form(form):
    required = {
        "trakt_client_id": "Trakt Client ID",
        "trakt_client_secret": "Trakt Client Secret",
        "trakt_username": "Trakt Username",
        "gmail_sender": "Gmail sender address",
        "gmail_app_password": "Gmail App Password",
    }
    values = {key: form.get(key, "").strip() for key in required}
    missing = [label for key, label in required.items() if not values[key]]
    if missing:
        raise RuntimeError(f"Missing required setup fields: {', '.join(missing)}.")

    # PIN is optional: without one, setup saves the app keys and the user signs in with a
    # device code on the Credentials page (Trakt's PIN page often hangs).
    pin = form.get("trakt_pin", "").strip()
    token_data = (
        _exchange_trakt_pin(values["trakt_client_id"], values["trakt_client_secret"], pin)
        if pin else {"access_token": "", "refresh_token": ""}
    )
    notify_email = form.get("notify_email", "").strip() or values["gmail_sender"]
    return {
        "trakt_client_id": values["trakt_client_id"],
        "trakt_client_secret": values["trakt_client_secret"],
        "trakt_access_token": token_data["access_token"],
        "trakt_refresh_token": token_data.get("refresh_token", ""),
        "trakt_username": values["trakt_username"],
        "tmdb_api_key": form.get("tmdb_api_key", "").strip(),
        "gmail_sender": values["gmail_sender"],
        "gmail_app_password": values["gmail_app_password"],
        "notify_email": notify_email,
    }


def _exchange_trakt_pin(client_id, client_secret, pin):
    try:
        resp = requests.post(
            TRAKT_OAUTH_TOKEN,
            json={
                "code": pin,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": TRAKT_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            headers={
                "User-Agent": "Streamsaver/2.5",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError("Trakt token exchange failed. Check the Client ID, Client Secret, and PIN.") from exc
    if not data.get("access_token"):
        raise RuntimeError("Trakt token exchange did not return an access token.")
    return data


def _trakt_client(creds, token_refresh_callback=None, token_loader=None):
    return TraktClient(
        client_id=creds["trakt_client_id"],
        client_secret=creds.get("trakt_client_secret", ""),
        access_token=creds["trakt_access_token"],
        refresh_token=creds.get("trakt_refresh_token", ""),
        username=creds.get("trakt_username", ""),
        token_refresh_callback=token_refresh_callback,
        token_loader=token_loader,
    )


def _tmdb_client(creds):
    key = creds.get("tmdb_api_key", "")
    if not key:
        return None
    return TMDbClient(api_key=key, country=_get_setting("provider_country") or "US")


def _show_counts():
    with db_session() as conn:
        rows = conn.execute(
            "SELECT show_state, COUNT(*) AS count FROM shows GROUP BY show_state ORDER BY show_state"
        ).fetchall()
    return {row["show_state"] or "unknown": row["count"] for row in rows}


def _show_sort(sort, direction):
    sort = sort if sort in SHOW_SORT_COLUMNS else "score"
    direction = "asc" if direction == "asc" else "desc"
    return sort, direction


def _shows(q, state, sort="score", direction="desc"):
    sort, direction = _show_sort(sort, direction)
    query = """
        SELECT s.trakt_id, s.title, s.show_state, s.interest_score,
               s.trakt_status, s.primary_provider, s.last_watch_date,
               s.next_air_date, svc.name AS service_name
        FROM shows s
        LEFT JOIN services svc ON s.service_id = svc.id
        WHERE 1 = 1
    """
    params = []
    if q:
        query += " AND LOWER(s.title) LIKE ?"
        params.append(f"%{q.lower()}%")
    if state and state != "All":
        query += " AND LOWER(s.show_state) = ?"
        params.append(state.lower())
    query += f" ORDER BY {SHOW_SORT_COLUMNS[sort]} {direction.upper()}, LOWER(s.title) ASC"
    with db_session() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _service_payload(form):
    name = form.get("name", "").strip()
    network_provider_id = get_network_provider_id(name)
    tmdb_provider_id = _int_or_none(form.get("tmdb_provider_id")) or network_provider_id
    billing_cycle = form.get("billing_cycle", "monthly")
    return {
        "name": name,
        "status": form.get("status", "not_subscribed"),
        "monthly_cost": _float(form.get("monthly_cost")),
        "annual_cost": _float(form.get("annual_cost")),
        "billing_cycle": billing_cycle,
        "billing_anchor_date": form.get("billing_anchor_date") or None,
        "next_billing_date": form.get("billing_anchor_date") or None,
        "auto_renew": 1 if form.get("auto_renew") == "on" else 0,
        "tmdb_provider_id": tmdb_provider_id,
    }


def _bundle_payload(raw):
    providers = []
    with db_session() as conn:
        for part in (raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                provider_id = int(part)
            except ValueError:
                continue
            row = conn.execute("SELECT name FROM networks WHERE provider_id = ?", (provider_id,)).fetchone()
            providers.append(
                {
                    "tmdb_provider_id": provider_id,
                    "provider_name": row["name"] if row else str(provider_id),
                }
            )
    return providers


def _send_all_clear(creds, notify_email):
    html, attachments = build_all_clear(run_analysis())
    send_report(
        to_address=notify_email,
        subject="Streamsaver: All clear - no changes recommended",
        html_body=html,
        attachments=attachments,
        from_address=creds.get("gmail_sender", ""),
        app_password=creds.get("gmail_app_password", ""),
    )


def _send_analysis_reports(creds, notify_email):
    results = run_analysis()
    email_results, included_status_alerts = filter_status_alerts_for_frequency(results)
    from_address = creds.get("gmail_sender", "")
    app_password = creds.get("gmail_app_password", "")
    if not from_address or not app_password:
        raise RuntimeError("Gmail sender/app password missing from credentials.")
    report_types = [
        ("suspend", "Consider suspending"),
        ("consider_non_renewal", "Annual renewal to review"),
        ("renew_soon", "Annual renewal approaching"),
        ("resubscribe", "Time to resubscribe"),
    ]
    sent = 0
    for report_type, subject_prefix in report_types:
        recs = email_results.get(report_type, [])
        if not recs:
            continue
        names = ", ".join(row["name"] for row in recs)
        html, attachments = build_report(report_type, recs)
        send_report(
            to_address=notify_email,
            subject=f"Streamsaver: {subject_prefix} - {names}",
            html_body=html,
            attachments=attachments,
            from_address=from_address,
            app_password=app_password,
        )
        sent += 1
    if sent and included_status_alerts and has_status_alerts(email_results):
        mark_status_alerts_sent()
    return sent


def _float(value):
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
