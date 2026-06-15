import os
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from flask import (
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
from core.show_tracker import run_full_sync
from core.tmdb_client import TMDbClient
from core.trakt_client import TraktClient
from db import db_session, init_db
from email_reports.mailer import send_report
from email_reports.report_builder import build_report
from setup_credentials import CRED_FILE, load_credentials

logger = logging.getLogger(__name__)


THRESHOLD_OPTIONS = [
    ("1 month (30 days)", "30"),
    ("3 months (90 days)", "90"),
    ("6 months (180 days)", "180"),
    ("12 months (365 days)", "365"),
]


def create_app():
    _setup_logging()
    init_db()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.environ.get("STREAMSAVER_SECRET_KEY", "streamsaver-local-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["CREDS"] = None

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

    @app.get("/health")
    def health():
        return {"ok": True, "unlocked": bool(_credentials(optional=True))}

    @app.get("/logos/<path:filename>")
    def logos(filename):
        return send_from_directory(Path(__file__).resolve().parent.parent / "assets" / "logos", filename)

    @app.route("/unlock", methods=["GET", "POST"])
    def unlock():
        if request.method == "POST":
            passphrase = request.form.get("passphrase", "").strip()
            try:
                app.config["CREDS"] = load_credentials(passphrase)
                session["unlocked_at"] = datetime.now().isoformat(timespec="seconds")
                flash("Streamsaver unlocked.", "success")
                return redirect(url_for("dashboard"))
            except Exception as exc:
                flash(f"Could not unlock credentials: {exc}", "error")
        return render_template("unlock.html", has_credentials=Path(CRED_FILE).exists())

    @app.post("/lock")
    def lock():
        app.config["CREDS"] = None
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
            flash("Service deleted.", "success")
        except Exception as exc:
            flash(f"Could not delete service: {exc}", "error")
        return redirect(url_for("services"))

    @app.get("/shows")
    def shows():
        q = request.args.get("q", "").strip()
        state = request.args.get("state", "All")
        rows = _shows(q, state)
        return render_template("shows.html", shows=rows, q=q, state=state)

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
            _set_setting("abandoned_mode", request.form.get("abandoned_mode", "manual"))
            _set_setting("abandoned_threshold_days", request.form.get("abandoned_threshold_days", "90"))
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        return render_template("settings.html", settings=_settings(), thresholds=THRESHOLD_OPTIONS)

    @app.post("/actions/sync")
    def action_sync():
        try:
            creds = _credentials()
            force_backfill = request.form.get("force_backfill") == "1"
            results = run_full_sync(_trakt_client(creds), _tmdb_client(creds), force_backfill=force_backfill)
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
            flash(f"Analysis complete. {sent} email(s) sent.", "success")
        except Exception as exc:
            flash(f"Analysis failed: {exc}", "error")
        return redirect(request.referrer or url_for("dashboard"))

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
                return app.config["CREDS"]
            except Exception:
                if not optional:
                    raise
        if optional:
            return None
        raise RuntimeError("Streamsaver is locked. Unlock credentials first.")

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
        "abandoned_mode",
        "abandoned_threshold_days",
        "last_trakt_sync",
        "networks_fetched_at",
    ]
    return {key: _get_setting(key) for key in keys}


def _trakt_client(creds):
    return TraktClient(
        client_id=creds["trakt_client_id"],
        client_secret=creds.get("trakt_client_secret", ""),
        access_token=creds["trakt_access_token"],
        refresh_token=creds.get("trakt_refresh_token", ""),
        username=creds.get("trakt_username", ""),
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


def _shows(q, state):
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
    query += " ORDER BY s.interest_score DESC, LOWER(s.title)"
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


def _send_analysis_reports(creds, notify_email):
    results = run_analysis()
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
        recs = results.get(report_type, [])
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
