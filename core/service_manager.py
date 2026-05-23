"""
core/service_manager.py
Streamsaver — Streaming service CRUD and cost logic
Author: Noah Lee

Manages the `services` table: add, update, delete services, status transitions,
and cost roll-up calculations.

Valid status values: subscribed | suspended | unsubscribed | not_subscribed
"""

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

import requests

from db import db_session

logger = logging.getLogger(__name__)

VALID_STATUSES = {"subscribed", "suspended", "unsubscribed", "not_subscribed"}

_BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGOS_DIR = os.path.join(_BASE_DIR, "assets", "logos")
_TMDB_IMG  = "https://image.tmdb.org/t/p/w92"

ICON_TTL_DAYS = 30   # Re-download logos older than this many days


# ── Icon helpers ───────────────────────────────────────────────────────────────

def _logo_path_for(provider_id: int) -> str:
    """Return the RELATIVE path for a provider logo (stored in DB and on disk)."""
    return os.path.join("assets", "logos", f"{provider_id}.jpg")


def _logo_abs_path(provider_id: int) -> str:
    """Return the absolute path for a provider logo (used for file I/O)."""
    return os.path.join(_LOGOS_DIR, f"{provider_id}.jpg")


def download_icon_for_provider(
    provider_id: int,
    logo_path: str,
    force: bool = False,
) -> Optional[str]:
    """
    Download a TMDb provider logo to assets/logos/{provider_id}.jpg.
    Returns the RELATIVE path on success, None on failure.
    Skips download if the file already exists unless force=True.
    """
    if not logo_path:
        return None
    os.makedirs(_LOGOS_DIR, exist_ok=True)
    rel  = _logo_path_for(provider_id)
    dest = _logo_abs_path(provider_id)
    if os.path.exists(dest) and not force:
        return rel
    try:
        url  = f"{_TMDB_IMG}{logo_path}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.debug("Downloaded icon for provider %d.", provider_id)
        return rel
    except Exception as exc:
        logger.debug("Could not download icon for provider %d: %s", provider_id, exc)
        return None


def get_icon_for_service_name(name: str) -> Optional[str]:
    """
    Return a local logo path for the given provider name.
    Checks the logos cache on disk; returns None if not found.
    """
    with db_session() as conn:
        row = conn.execute(
            "SELECT provider_id, logo_path FROM networks WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()
    if not row or not row["provider_id"]:
        return None
    path = _logo_path_for(row["provider_id"])
    return path if os.path.exists(path) else None


def backfill_service_icons() -> int:
    """
    For every service without an icon_path, resolve a logo via three strategies:
      1. Exact name match against the networks cache.
      2. Bundle provider lookup — use the first bundle provider that has a logo.
      3. Download from TMDb if the file is missing.
    Also migrates stale absolute paths and old service_icons directory paths to
    relative paths so the project is portable across machines.
    Returns the number of services updated.
    """
    with db_session() as conn:
        # Migrate old service_icons directory paths
        conn.execute(
            "UPDATE services SET icon_path = REPLACE(icon_path, '/assets/service_icons/', '/assets/logos/') "
            "WHERE icon_path LIKE '%/assets/service_icons/%'"
        )
        # Migrate absolute paths → relative (strip everything before 'assets/')
        conn.execute(
            "UPDATE services SET icon_path = SUBSTR(icon_path, INSTR(icon_path, 'assets/')) "
            "WHERE icon_path LIKE '/%' AND icon_path LIKE '%/assets/logos/%'"
        )
        # Clean up name typos
        conn.execute(
            "UPDATE services SET name = TRIM(TRIM(name, '=')) WHERE name LIKE '%='"
        )

        services = conn.execute(
            "SELECT id, name FROM services WHERE icon_path IS NULL OR icon_path = ''"
        ).fetchall()
        networks_map = {
            r["name"].lower(): (r["provider_id"], r["logo_path"])
            for r in conn.execute(
                "SELECT name, provider_id, logo_path FROM networks WHERE logo_path != ''"
            ).fetchall()
        }
        bundle_map: dict[int, list[tuple[int, str]]] = {}
        for r in conn.execute(
            """SELECT sb.service_id, sb.tmdb_provider_id, n.logo_path
               FROM service_bundles sb
               JOIN networks n ON n.provider_id = sb.tmdb_provider_id
               WHERE n.logo_path != ''
               ORDER BY sb.rowid"""
        ).fetchall():
            bundle_map.setdefault(r["service_id"], []).append(
                (r["tmdb_provider_id"], r["logo_path"])
            )

    updated = 0
    for svc in services:
        provider_id, logo_path = None, None

        match = networks_map.get(svc["name"].lower())
        if match:
            provider_id, logo_path = match

        if not provider_id:
            for pid, lpath in bundle_map.get(svc["id"], []):
                provider_id, logo_path = pid, lpath
                break

        if not provider_id:
            continue

        local = download_icon_for_provider(provider_id, logo_path)
        if local:
            with db_session() as conn:
                conn.execute(
                    "UPDATE services SET icon_path = ?, tmdb_provider_id = ?, icon_last_updated = ? WHERE id = ?",
                    (local, provider_id, date.today().isoformat(), svc["id"]),
                )
            updated += 1
            time.sleep(0.1)

    if updated:
        logger.info("Backfilled icons for %d service(s).", updated)

    refresh_stale_icons()
    return updated


def refresh_stale_icons() -> int:
    """
    Re-download logos for services whose icon_last_updated is older than
    ICON_TTL_DAYS or has never been set. Logos rarely change; this just
    ensures they stay current without hammering TMDb on every run.
    Returns the number of logos refreshed.
    """
    cutoff = (date.today() - timedelta(days=ICON_TTL_DAYS)).isoformat()

    with db_session() as conn:
        stale = conn.execute(
            """SELECT s.id, s.tmdb_provider_id, n.logo_path, s.icon_last_updated
               FROM services s
               JOIN networks n ON n.provider_id = s.tmdb_provider_id
               WHERE s.icon_path IS NOT NULL AND s.icon_path != ''
                 AND (s.icon_last_updated IS NULL OR s.icon_last_updated < ?)""",
            (cutoff,),
        ).fetchall()

    if not stale:
        logger.debug("All service icons are up to date (TTL: %d days).", ICON_TTL_DAYS)
        return 0

    today = date.today().isoformat()
    refreshed = 0
    for row in stale:
        local = download_icon_for_provider(row["tmdb_provider_id"], row["logo_path"], force=True)
        if local:
            with db_session() as conn:
                conn.execute(
                    "UPDATE services SET icon_path = ?, icon_last_updated = ? WHERE id = ?",
                    (local, today, row["id"]),
                )
            refreshed += 1
            time.sleep(0.15)

    if refreshed:
        logger.info("Refreshed %d stale icon(s) (TTL: %d days).", refreshed, ICON_TTL_DAYS)
    return refreshed


def fetch_and_cache_networks(tmdb_client) -> int:
    """
    Pull the TV streaming provider list from TMDb and store it in the local
    `networks` table. Replaces all existing cached entries.
    Returns the number of providers stored.
    """
    providers = tmdb_client.get_streaming_providers()
    if not providers:
        logger.warning("TMDb returned no streaming providers.")
        return 0

    today = date.today().isoformat()
    with db_session() as conn:
        conn.execute("DELETE FROM networks")
        conn.executemany(
            """INSERT OR REPLACE INTO networks (name, provider_id, logo_path, country, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    p.get("provider_name", "").strip(),
                    p.get("provider_id"),
                    p.get("logo_path", ""),
                    tmdb_client.country,
                    today,
                )
                for p in providers
                if p.get("provider_name", "").strip()
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('networks_fetched_at', ?)",
            (today,),
        )

    logger.info("Cached %d providers from TMDb (%s).", len(providers), tmdb_client.country)
    backfill_service_icons()
    return len(providers)


def get_cached_network_names() -> list[str]:
    """Return sorted list of all cached network names."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT name FROM networks ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [r["name"] for r in rows]


def networks_cache_is_empty() -> bool:
    """Return True if the networks table has no rows."""
    with db_session() as conn:
        count = conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
    return count == 0


def get_network_provider_id(name: str) -> Optional[int]:
    """Return the TMDb provider_id for a given provider name, or None."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT provider_id FROM networks WHERE name = ?", (name,)
        ).fetchone()
    return row["provider_id"] if row else None


def estimated_monthly_spend(svc: dict) -> float:
    """Derive a normalized monthly spend regardless of billing cycle."""
    cycle = svc.get("billing_cycle", "monthly")
    if cycle == "annual":
        return round((svc.get("annual_cost") or 0.0) / 12, 2)
    return svc.get("monthly_cost") or 0.0


def estimated_annual_spend(svc: dict) -> float:
    """Derive a normalized annual spend regardless of billing cycle."""
    cycle = svc.get("billing_cycle", "monthly")
    if cycle == "monthly":
        return round((svc.get("monthly_cost") or 0.0) * 12, 2)
    return svc.get("annual_cost") or 0.0


# ── CRUD ───────────────────────────────────────────────────────────────────────

def add_service(
    name: str,
    status: str = "not_subscribed",
    monthly_cost: float = 0.0,
    annual_cost: float = 0.0,
    next_billing_date: Optional[str] = None,
    trakt_network_id: Optional[str] = None,
    tmdb_provider_id: Optional[int] = None,
    icon_path: Optional[str] = None,
    billing_cycle: str = "monthly",
    billing_anchor_date: Optional[str] = None,
    auto_renew: int = 1,
) -> int:
    """
    Insert a new service. Returns the new row id.
    Auto-downloads the provider icon from TMDb cache if not already supplied.
    billing_cycle should be 'monthly' or 'annual'; only supply the matching cost field.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

    # Auto-detect billing cycle from cost fields if not explicitly set
    if billing_cycle == "monthly" and annual_cost > 0 and monthly_cost == 0:
        billing_cycle = "annual"

    # Clear the cost that doesn't match the cycle (no fake derived values)
    if billing_cycle == "annual":
        monthly_cost = 0.0
    else:
        annual_cost = 0.0

    if not billing_anchor_date:
        billing_anchor_date = next_billing_date

    # Auto-resolve icon from network cache if not provided
    if not icon_path:
        with db_session() as conn:
            row = conn.execute(
                "SELECT provider_id, logo_path FROM networks WHERE LOWER(name) = LOWER(?)",
                (name,),
            ).fetchone()
        if row and row["provider_id"]:
            if not tmdb_provider_id:
                tmdb_provider_id = row["provider_id"]
            icon_path = download_icon_for_provider(row["provider_id"], row["logo_path"])

    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO services
                (name, trakt_network_id, tmdb_provider_id, icon_path, status,
                 monthly_cost, annual_cost, next_billing_date,
                 billing_cycle, billing_anchor_date, auto_renew)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, trakt_network_id, tmdb_provider_id, icon_path, status,
             monthly_cost, annual_cost, next_billing_date,
             billing_cycle, billing_anchor_date, auto_renew),
        )
        return cursor.lastrowid


def update_service(
    service_id: int,
    name: Optional[str] = None,
    status: Optional[str] = None,
    monthly_cost: Optional[float] = None,
    annual_cost: Optional[float] = None,
    next_billing_date: Optional[str] = None,
    trakt_network_id: Optional[str] = None,
    tmdb_provider_id: Optional[int] = None,
    icon_path: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    billing_anchor_date: Optional[str] = None,
    auto_renew: Optional[int] = None,
) -> None:
    """Update one or more fields on an existing service."""
    if status and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'.")

    fields = []
    values = []

    updates = {
        "name":                name,
        "status":              status,
        "monthly_cost":        monthly_cost,
        "annual_cost":         annual_cost,
        "next_billing_date":   next_billing_date,
        "trakt_network_id":    trakt_network_id,
        "tmdb_provider_id":    tmdb_provider_id,
        "icon_path":           icon_path,
        "billing_cycle":       billing_cycle,
        "billing_anchor_date": billing_anchor_date,
        "auto_renew":          auto_renew,
    }
    for col, val in updates.items():
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)

    if not fields:
        return

    values.append(service_id)
    with db_session() as conn:
        conn.execute(
            f"UPDATE services SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def delete_service(service_id: int) -> None:
    """
    Delete a service. Shows assigned to this service will have their
    service_id set to NULL (via FK ON DELETE SET NULL).
    """
    with db_session() as conn:
        conn.execute("DELETE FROM services WHERE id = ?", (service_id,))


def get_service(service_id: int) -> Optional[dict]:
    """Return a single service as a dict, or None if not found."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM services WHERE id = ?", (service_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_services() -> list[dict]:
    """Return all services ordered by name."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM services ORDER BY LOWER(name)"
        ).fetchall()
        return [dict(r) for r in rows]


def get_subscribed_services() -> list[dict]:
    """Return only services with status = 'subscribed'."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM services WHERE status = 'subscribed' ORDER BY LOWER(name)"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Status transitions ─────────────────────────────────────────────────────────

def set_service_status(service_id: int, new_status: str) -> None:
    """Shortcut to change only the status of a service."""
    update_service(service_id, status=new_status)
    logger.info("Service %d status → %s", service_id, new_status)


# ── Cost calculations ──────────────────────────────────────────────────────────

def get_cost_totals(status_filter: Optional[str] = None) -> dict:
    """
    Calculate total estimated monthly and annual spend.

    Uses estimated_monthly_spend / estimated_annual_spend (derived at runtime)
    so the totals reflect real subscription cost regardless of billing cycle.

    Args:
        status_filter: If provided, only include services matching this status.

    Returns:
        dict with keys: monthly_total, annual_total, services (list of dicts)
    """
    services = get_all_services()
    if status_filter:
        services = [s for s in services if s["status"] == status_filter]

    monthly_total = sum(estimated_monthly_spend(s) for s in services)
    annual_total  = sum(estimated_annual_spend(s)  for s in services)

    return {
        "monthly_total": round(monthly_total, 2),
        "annual_total":  round(annual_total,  2),
        "services":      services,
    }


# ── Bundle provider CRUD ───────────────────────────────────────────────────────

def get_bundle_providers(service_id: int) -> list[dict]:
    """Return all bundle provider mappings for a service."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT tmdb_provider_id, provider_name FROM service_bundles WHERE service_id = ? ORDER BY provider_name",
            (service_id,),
        ).fetchall()
    return [{"tmdb_provider_id": r["tmdb_provider_id"], "provider_name": r["provider_name"]} for r in rows]


def set_bundle_providers(service_id: int, providers: list[dict]) -> None:
    """
    Replace all bundle providers for a service in one operation.

    Args:
        service_id: The service to update.
        providers:  List of dicts with keys tmdb_provider_id (int) and provider_name (str).
    """
    with db_session() as conn:
        conn.execute("DELETE FROM service_bundles WHERE service_id = ?", (service_id,))
        for p in providers:
            conn.execute(
                "INSERT OR IGNORE INTO service_bundles (service_id, tmdb_provider_id, provider_name) VALUES (?, ?, ?)",
                (service_id, p["tmdb_provider_id"], p.get("provider_name", "")),
            )


# ── Trakt network import ───────────────────────────────────────────────────────

def import_networks_from_trakt(client) -> int:
    """
    Fetch all networks from Trakt and add any that don't already exist
    in the services table (with status = 'not_subscribed').
    Returns count of newly added services.
    """
    networks = client.get_networks()
    added = 0
    with db_session() as conn:
        for net in networks:
            name = net.get("name", "").strip()
            if not name:
                continue
            exists = conn.execute(
                "SELECT 1 FROM services WHERE LOWER(TRIM(name)) = LOWER(?)", (name,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO services (name, trakt_network_id, status)
                    VALUES (?, ?, 'not_subscribed')
                    """,
                    (name, name),
                )
                added += 1

    logger.info("Imported %d new networks from Trakt.", added)
    return added
