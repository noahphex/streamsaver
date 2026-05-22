"""
core/service_manager.py
Streamsaver — Streaming service CRUD and cost logic
Author: Noah Lee

Manages the `services` table: add, update, delete services, status transitions,
and cost roll-up calculations.

Valid status values: subscribed | suspended | unsubscribed | not_subscribed
"""

import logging
from datetime import date
from typing import Optional

from db import db_session

logger = logging.getLogger(__name__)

VALID_STATUSES = {"subscribed", "suspended", "unsubscribed", "not_subscribed"}


# ── Network cache ──────────────────────────────────────────────────────────────

def fetch_and_cache_networks(client) -> int:
    """
    Pull the full network list from Trakt and store it in the local `networks`
    table.  Replaces all existing cached entries.
    Returns the number of networks stored.
    """
    networks = client.get_networks()
    if not networks:
        logger.warning("Trakt returned no networks.")
        return 0

    today = date.today().isoformat()
    with db_session() as conn:
        conn.execute("DELETE FROM networks")
        conn.executemany(
            "INSERT OR REPLACE INTO networks (name, country, fetched_at) VALUES (?,?,?)",
            [
                (
                    net.get("name", "").strip(),
                    net.get("country", ""),
                    today,
                )
                for net in networks
                if net.get("name", "").strip()
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('networks_fetched_at', ?)",
            (today,),
        )

    logger.info("Cached %d networks from Trakt.", len(networks))
    return len(networks)


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


# ── CRUD ───────────────────────────────────────────────────────────────────────

def add_service(
    name: str,
    status: str = "not_subscribed",
    monthly_cost: float = 0.0,
    annual_cost: float = 0.0,
    next_billing_date: Optional[str] = None,
    trakt_network_id: Optional[str] = None,
    icon_path: Optional[str] = None,
) -> int:
    """
    Insert a new service. Returns the new row id.
    If annual_cost is 0 and monthly_cost > 0, annual_cost is auto-calculated.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

    if annual_cost == 0.0 and monthly_cost > 0:
        annual_cost = round(monthly_cost * 12, 2)

    with db_session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO services
                (name, trakt_network_id, icon_path, status,
                 monthly_cost, annual_cost, next_billing_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, trakt_network_id, icon_path, status,
             monthly_cost, annual_cost, next_billing_date),
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
    icon_path: Optional[str] = None,
) -> None:
    """Update one or more fields on an existing service."""
    if status and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'.")

    fields = []
    values = []

    updates = {
        "name":              name,
        "status":            status,
        "monthly_cost":      monthly_cost,
        "annual_cost":       annual_cost,
        "next_billing_date": next_billing_date,
        "trakt_network_id":  trakt_network_id,
        "icon_path":         icon_path,
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
    Calculate total monthly and annual costs.

    Args:
        status_filter: If provided, only include services matching this status.
                       Pass None to include all services.

    Returns:
        dict with keys: monthly_total, annual_total, services (list of dicts)
    """
    services = get_all_services()
    if status_filter:
        services = [s for s in services if s["status"] == status_filter]

    monthly_total = sum(s["monthly_cost"] or 0.0 for s in services)
    annual_total  = sum(s["annual_cost"]  or 0.0 for s in services)

    return {
        "monthly_total": round(monthly_total, 2),
        "annual_total":  round(annual_total,  2),
        "services":      services,
    }


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
