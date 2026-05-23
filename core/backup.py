"""
core/backup.py
Streamsaver — Backup and restore user data (services + settings).
Author: Noah Lee

Backup format: JSON  (human-readable, portable)
  {
    "version": 1,
    "exported_at": "<ISO-8601 UTC>",
    "settings": { "<key>": "<value>", ... },
    "services": [
      {
        ...all service columns (icon_path excluded)...,
        "bundles": [{"tmdb_provider_id": int, "provider_name": str}, ...]
      },
      ...
    ]
  }

Restore is a clean-slate replace:
  1. Delete all existing service_bundles and services.
  2. Re-insert services (auto-assigned new IDs).
  3. Re-insert their bundles.
    4. Merge settings (only non-runtime keys; sync timestamps are left alone).
    5. Immediately backfill service icons from the cached provider map.

icon_path is intentionally excluded — logos are re-fetched from TMDb
automatically on the next provider-list refresh.
"""

import json
import logging
import os
from datetime import datetime, timezone

from db import db_session

logger = logging.getLogger(__name__)

BACKUP_VERSION = 1

# Settings keys that represent runtime/sync state — skip on restore
_RUNTIME_SETTINGS = {"last_trakt_sync", "networks_fetched_at"}

# Service columns to include in the backup (icon_path excluded)
_SERVICE_COLS = [
    "name",
    "trakt_network_id",
    "tmdb_provider_id",
    "status",
    "monthly_cost",
    "annual_cost",
    "billing_cycle",
    "billing_anchor_date",
    "auto_renew",
    "next_billing_date",
]


def export_backup(path: str) -> int:
    """
    Write a JSON backup to *path*.
    Returns the number of services exported.
    Raises OSError on write failure.
    """
    with db_session() as conn:
        # --- settings ---
        settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
        settings = {
            r["key"]: r["value"]
            for r in settings_rows
            if r["key"] not in _RUNTIME_SETTINGS
        }

        # --- services + bundles ---
        service_rows = conn.execute(
            f"SELECT id, {', '.join(_SERVICE_COLS)} FROM services ORDER BY id"
        ).fetchall()

        services = []
        for svc in service_rows:
            svc_dict = {col: svc[col] for col in _SERVICE_COLS}
            bundles = conn.execute(
                "SELECT tmdb_provider_id, provider_name FROM service_bundles WHERE service_id = ?",
                (svc["id"],),
            ).fetchall()
            svc_dict["bundles"] = [
                {"tmdb_provider_id": b["tmdb_provider_id"], "provider_name": b["provider_name"]}
                for b in bundles
            ]
            services.append(svc_dict)

    payload = {
        "version":     BACKUP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings":    settings,
        "services":    services,
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("Backup exported to %s (%d services)", path, len(services))
    return len(services)


def import_backup(path: str) -> tuple[int, int]:
    """
    Restore from a JSON backup at *path* (clean-slate replace).
    Automatically attempts to repopulate service icon_path values after restore
    so email logos work without a separate manual refresh step.
    Returns (services_restored, settings_restored).
    Raises ValueError on schema/version mismatch.
    Raises OSError on read failure.
    """
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    version = payload.get("version")
    if version != BACKUP_VERSION:
        raise ValueError(
            f"Unsupported backup version {version!r}. "
            f"Expected {BACKUP_VERSION}."
        )

    services = payload.get("services", [])
    settings = payload.get("settings", {})

    if not isinstance(services, list):
        raise ValueError("Backup 'services' field must be a list.")
    if not isinstance(settings, dict):
        raise ValueError("Backup 'settings' field must be a dict.")

    with db_session() as conn:
        # Clean slate — foreign_keys cascade deletes bundles automatically
        conn.execute("DELETE FROM service_bundles")
        conn.execute("DELETE FROM services")

        for svc in services:
            # Build parameterised INSERT from whichever columns are present
            cols_present = [c for c in _SERVICE_COLS if c in svc]
            placeholders = ", ".join("?" for _ in cols_present)
            col_names    = ", ".join(cols_present)
            values       = [svc[c] for c in cols_present]

            conn.execute(
                f"INSERT INTO services ({col_names}) VALUES ({placeholders})",
                values,
            )
            service_id = conn.execute(
                "SELECT id FROM services WHERE name = ?", (svc["name"],)
            ).fetchone()["id"]

            for bundle in svc.get("bundles", []):
                conn.execute(
                    """INSERT OR IGNORE INTO service_bundles
                       (service_id, tmdb_provider_id, provider_name)
                       VALUES (?, ?, ?)""",
                    (service_id, bundle["tmdb_provider_id"], bundle["provider_name"]),
                )

        # Restore settings (skip runtime keys)
        settings_count = 0
        for key, value in settings.items():
            if key in _RUNTIME_SETTINGS:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            settings_count += 1

    logger.info(
        "Backup restored from %s — %d services, %d settings",
        path, len(services), settings_count,
    )

    # Keep restore successful even if icon backfill encounters transient issues.
    # This runs after DB writes so restored records are visible to the backfill pass.
    try:
        from core.service_manager import backfill_service_icons

        updated_icons = backfill_service_icons()
        logger.info("Post-restore icon backfill updated %d service(s).", updated_icons)
    except Exception as exc:
        logger.warning("Post-restore icon backfill failed: %s", exc)

    return len(services), settings_count
