"""
scheduler.py
Streamsaver — Daily scheduled task runner
Author: Noah Lee

Runs once a day:
  1. Loads and decrypts credentials (passphrase via CLI argument or env var
     STREAMSAVER_PASSPHRASE — never hardcoded).
  2. Syncs Trakt watch history and watchlist.
  3. Runs the suspend / resubscribe analysis.
  4. Sends email reports for any recommendations.

Usage (run continuously — register with cron or Task Scheduler):
    python scheduler.py

Or to run the job once immediately and exit:
    python scheduler.py --run-once

Passphrase resolution order:
  1. Environment variable: STREAMSAVER_PASSPHRASE
  2. Command-line argument: --passphrase <value>
  3. Interactive prompt (stdin)
"""

import argparse
import getpass
import logging
import os
import sys
import time

import schedule

# Ensure the project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import init_db, db_session
from core.notifications import send_discord_notification
from core.newsletter import (
    filter_status_alerts_for_frequency,
    has_status_alerts,
    mark_status_alerts_sent,
)
from core.trakt_client import TraktAuthError, TraktClient
from core.tmdb_client  import TMDbClient
from core.show_tracker import run_full_sync
from core.analysis     import run_analysis
from email_reports.report_builder import build_digest
from email_reports.mailer          import send_report_with_credentials
from setup_credentials             import encrypt_credentials, load_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("streamsaver.scheduler")

# Run time: once per day at this hour (24h, local time)
RUN_AT = "02:00"


def _get_passphrase(args) -> str:
    """Resolve the passphrase from env, CLI arg, or interactive prompt."""
    if os.environ.get("STREAMSAVER_PASSPHRASE"):
        return os.environ["STREAMSAVER_PASSPHRASE"]
    if getattr(args, "passphrase", None):
        return args.passphrase
    return getpass.getpass("Streamsaver passphrase: ").strip()


def _get_setting(key: str) -> str:
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else ""


def _notify_discord(title: str, message: str, *, level: str = "error") -> None:
    webhook_url = _get_setting("discord_webhook_url")
    if not webhook_url:
        logger.info("No Discord webhook configured; skipping Discord notification.")
        return
    send_discord_notification(
        webhook_url,
        title,
        message,
        level=level,
        footer="Streamsaver scheduler",
    )


def run_daily_job(creds: dict, passphrase: str | None = None) -> bool:
    """
    Execute the full daily pipeline:
      sync → analyse → email recommendations.
    """
    logger.info("=" * 60)
    logger.info("Daily job started.")

    def _persist_trakt_tokens(tokens: dict) -> None:
        creds["trakt_access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            creds["trakt_refresh_token"] = tokens["refresh_token"]
        if not passphrase:
            logger.warning("Trakt token refreshed but no passphrase is available; token was not persisted.")
            return
        encrypt_credentials(creds, passphrase, quiet=True)
        logger.info("Persisted refreshed Trakt tokens.")

    # ── 1. Trakt sync ──────────────────────────────────────────────────────
    client = TraktClient(
        client_id     = creds["trakt_client_id"],
        client_secret = creds["trakt_client_secret"],
        access_token  = creds["trakt_access_token"],
        refresh_token = creds.get("trakt_refresh_token", ""),
        username      = creds.get("trakt_username", ""),
        token_refresh_callback = _persist_trakt_tokens,
    )

    tmdb_key = creds.get("tmdb_api_key", "")
    country  = _get_setting("provider_country") or "US"
    tmdb_client = TMDbClient(api_key=tmdb_key, country=country) if tmdb_key else None
    if not tmdb_client:
        logger.warning("No TMDb API key — provider lookup will be skipped during sync.")

    try:
        sync_results = run_full_sync(client, tmdb_client)
        logger.info(
            "Sync complete — shows: %d, watchlist: %d, archived: %d",
            sync_results["shows"],
            sync_results["watchlist"],
            sync_results["archived"],
        )
    except Exception as exc:
        logger.error("Trakt sync failed: %s", exc)
        if isinstance(exc, TraktAuthError):
            _notify_discord(
                "Streamsaver Trakt authorization failed",
                "The scheduled Trakt sync failed because Trakt rejected the saved access/refresh token. "
                "Open Streamsaver Settings -> Update Credentials, re-authorize Trakt, then run Sync again.\n\n"
                f"Error: {exc}",
            )
        else:
            _notify_discord(
                "Streamsaver scheduled sync failed",
                f"The scheduled Trakt sync failed before analysis could run.\n\nError: {exc}",
            )
        logger.info("=" * 60)
        return False

    # ── 2. Analysis ────────────────────────────────────────────────────────
    try:
        results = run_analysis()
        logger.info(
            "Analysis — keep: %d, suspend: %d, consider_non_renewal: %d, "
            "renew_soon: %d, resubscribe: %d, uncertain: %d",
            len(results.get("keep", [])),
            len(results.get("suspend", [])),
            len(results.get("consider_non_renewal", [])),
            len(results.get("renew_soon", [])),
            len(results.get("resubscribe", [])),
            len(results.get("uncertain", [])),
        )
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        _notify_discord(
            "Streamsaver analysis failed",
            f"The scheduled Trakt sync completed, but analysis failed.\n\nError: {exc}",
        )
        logger.info("=" * 60)
        return False

    # ── 3. Email digest ────────────────────────────────────────────────────
    notify_email = _get_setting("notification_email") or creds.get("notify_email", "")
    if not notify_email:
        logger.warning("No notification email configured — skipping email reports.")
        logger.info("Daily job complete.")
        logger.info("=" * 60)
        return True

    email_results, included_status_alerts = filter_status_alerts_for_frequency(results)
    html = build_digest(email_results)
    if not html:
        logger.info("No actionable recommendations today — no email sent.")
        logger.info("Daily job complete.")
        logger.info("=" * 60)
        return True

    html, attachments = html

    # Build a concise subject from what's in the digest
    subject_parts = []
    if email_results.get("suspend"):
        subject_parts.append(f"Suspend {len(email_results['suspend'])}")
    if email_results.get("consider_non_renewal"):
        subject_parts.append(f"Review {len(email_results['consider_non_renewal'])} renewal(s)")
    if email_results.get("renew_soon"):
        subject_parts.append(f"Renew {len(email_results['renew_soon'])} soon")
    if email_results.get("resubscribe"):
        subject_parts.append(f"Resubscribe {len(email_results['resubscribe'])}")
    subject = "Streamsaver Digest — " + " · ".join(subject_parts) if subject_parts else "Streamsaver Daily Digest"

    try:
        send_report_with_credentials(
            creds=creds,
            to_address=notify_email,
            subject=subject,
            html_body=html,
            attachments=attachments,
        )
        logger.info("Sent daily digest to %s", notify_email)
        if included_status_alerts and has_status_alerts(email_results):
            mark_status_alerts_sent()
    except Exception as exc:
        logger.error("Failed to send digest: %s", exc)
        _notify_discord(
            "Streamsaver email report failed",
            f"The scheduled sync and analysis completed, but sending the email digest failed.\n\nError: {exc}",
        )
        logger.info("=" * 60)
        return False

    logger.info("Daily job complete.")
    logger.info("=" * 60)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Streamsaver scheduler")
    parser.add_argument(
        "--run-once", action="store_true",
        help="Run the daily job immediately and exit (no loop).",
    )
    parser.add_argument(
        "--passphrase", default=None,
        help="Credential passphrase (prefer STREAMSAVER_PASSPHRASE env var).",
    )
    parser.add_argument(
        "--run-at", default=RUN_AT,
        help=f"Time to run the daily job in HH:MM format (default: {RUN_AT}).",
    )
    args = parser.parse_args()

    # Ensure DB tables exist
    init_db()

    # Load credentials once at startup
    passphrase = _get_passphrase(args)
    try:
        creds = load_credentials(passphrase)
    except Exception as exc:
        logger.error("Could not load credentials: %s", exc)
        try:
            _notify_discord(
                "Streamsaver scheduler could not load credentials",
                f"The scheduler could not decrypt Streamsaver credentials and did not run.\n\nError: {exc}",
            )
        except Exception:
            logger.debug("Could not send Discord credential-load failure notification.", exc_info=True)
        sys.exit(1)

    logger.info("Credentials loaded. Scheduler starting.")

    if args.run_once:
        ok = run_daily_job(creds, passphrase)
        sys.exit(0 if ok else 1)
        return

    # Schedule daily job and block
    schedule.every().day.at(args.run_at).do(run_daily_job, creds=creds, passphrase=passphrase)
    logger.info("Scheduled daily job at %s. Waiting…", args.run_at)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
