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
from core.trakt_client import TraktClient
from core.tmdb_client  import TMDbClient
from core.show_tracker import run_full_sync
from core.analysis     import run_analysis
from email_reports.report_builder import build_report
from email_reports.mailer          import send_report_with_credentials
from setup_credentials             import load_credentials

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


def run_daily_job(creds: dict) -> None:
    """
    Execute the full daily pipeline:
      sync → analyse → email recommendations.
    """
    logger.info("=" * 60)
    logger.info("Daily job started.")

    # ── 1. Trakt sync ──────────────────────────────────────────────────────
    client = TraktClient(
        client_id     = creds["trakt_client_id"],
        client_secret = creds["trakt_client_secret"],
        access_token  = creds["trakt_access_token"],
        refresh_token = creds.get("trakt_refresh_token", ""),
        username      = creds.get("trakt_username", ""),
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
        return

    # ── 2. Analysis ────────────────────────────────────────────────────────
    try:
        results = run_analysis()
        logger.info(
            "Analysis — suspend: %d, resubscribe: %d, ok: %d",
            len(results["suspend"]),
            len(results["resubscribe"]),
            len(results["ok"]),
        )
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        return

    # ── 3. Email reports ───────────────────────────────────────────────────
    notify_email = _get_setting("notification_email") or creds.get("notify_email", "")
    if not notify_email:
        logger.warning("No notification email configured — skipping email reports.")
        return

    sent = 0

    if results["suspend"]:
        names = ", ".join(r["name"] for r in results["suspend"])
        try:
            html = build_report("suspend", results["suspend"])
            send_report_with_credentials(
                creds=creds,
                to_address=notify_email,
                subject=f"Streamsaver: Consider suspending — {names}",
                html_body=html,
            )
            logger.info("Sent suspend digest for: %s", names)
            sent += 1
        except Exception as exc:
            logger.error("Failed to send suspend digest: %s", exc)

    if results["resubscribe"]:
        names = ", ".join(r["name"] for r in results["resubscribe"])
        try:
            html = build_report("resubscribe", results["resubscribe"])
            send_report_with_credentials(
                creds=creds,
                to_address=notify_email,
                subject=f"Streamsaver: Time to resubscribe — {names}",
                html_body=html,
            )
            logger.info("Sent resubscribe digest for: %s", names)
            sent += 1
        except Exception as exc:
            logger.error("Failed to send resubscribe digest: %s", exc)

    logger.info("Daily job complete. %d email(s) sent.", sent)
    logger.info("=" * 60)


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
        sys.exit(1)

    logger.info("Credentials loaded. Scheduler starting.")

    if args.run_once:
        run_daily_job(creds)
        return

    # Schedule daily job and block
    schedule.every().day.at(args.run_at).do(run_daily_job, creds=creds)
    logger.info("Scheduled daily job at %s. Waiting…", args.run_at)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
