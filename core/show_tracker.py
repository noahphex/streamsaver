"""
core/show_tracker.py
Streamsaver — Show data sync: Trakt → SQLite
Author: Noah Lee

Parses Trakt watch history and watchlist into the local database,
enriching each show with season/episode metadata and next-premiere info.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from db import db_session
from core.trakt_client import TraktClient

logger = logging.getLogger(__name__)

# ISO date format used by Trakt for first_aired fields
_TRAKT_DT_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """Safely parse a Trakt ISO datetime string and return YYYY-MM-DD, or None."""
    if not raw:
        return None
    for fmt in (_TRAKT_DT_FMT, "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _format_episode(season: Optional[int], episode: Optional[int]) -> Optional[str]:
    """Return a human-readable episode label like S02E05, or None."""
    if season is None or episode is None:
        return None
    return f"S{season:02d}E{episode:02d}"


def _resolve_service_id(network_name: Optional[str], conn) -> Optional[int]:
    """
    Return the service id that corresponds to a Trakt network name.

    Matching priority:
      1. Exact match on trakt_network_id (set when service was added from the
         Trakt network picker — guaranteed identical string)
      2. Case-insensitive name match as fallback for manually-entered services
    """
    if not network_name:
        return None

    # Priority 1: exact trakt_network_id match
    row = conn.execute(
        "SELECT id FROM services WHERE trakt_network_id = ?",
        (network_name,),
    ).fetchone()
    if row:
        return row["id"]

    # Priority 2: case-insensitive name fallback
    row = conn.execute(
        "SELECT id FROM services WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (network_name,),
    ).fetchone()
    return row["id"] if row else None


# ── Show sync ──────────────────────────────────────────────────────────────────

def sync_watched_shows(client: TraktClient) -> int:
    """
    Pull watched shows from Trakt, enrich with show/episode details,
    and upsert into the `shows` table.

    Returns the number of shows processed.
    """
    logger.info("Fetching watched shows from Trakt...")
    watched = client.get_watched_shows()
    if not watched:
        logger.info("No watched shows returned.")
        return 0

    count = 0
    for entry in watched:
        show_stub = entry.get("show", {})
        trakt_id  = str(show_stub.get("ids", {}).get("trakt", ""))
        title     = show_stub.get("title", "Unknown")

        if not trakt_id:
            logger.warning("Skipping show with no Trakt ID: %s", title)
            continue

        try:
            _sync_single_show(client, trakt_id, title, entry)
            count += 1
        except Exception as exc:
            logger.error("Error syncing show '%s' (%s): %s", title, trakt_id, exc)

    logger.info("Synced %d watched shows.", count)
    return count


def _sync_single_show(
    client: TraktClient,
    trakt_id: str,
    title: str,
    watched_entry: dict,
) -> None:
    """Fetch full details for one show and upsert into the database."""
    summary  = client.get_show_summary(trakt_id) or {}
    progress = None
    try:
        progress = client.get_show_progress(trakt_id)
    except Exception as exc:
        logger.debug("Could not fetch progress for %s: %s", trakt_id, exc)

    seasons      = client.get_show_seasons(trakt_id) or []
    next_ep_data = client.get_next_episode(trakt_id)

    # ── Derive fields ──────────────────────────────────────────────────────
    total_seasons = len([s for s in seasons if s.get("number", 0) > 0])
    total_eps     = summary.get("aired_episodes", 0)
    show_status   = summary.get("status", "")
    network       = summary.get("network", "")

    # Current season = last season in which user has watched any episode
    current_season = 0
    last_watched   = None
    last_watch_date = None

    if progress:
        seasons_prog = progress.get("seasons", [])
        for s in reversed(seasons_prog):
            completed = s.get("completed", 0)
            if completed and completed > 0:
                current_season = s.get("number", 0)
                # Find last watched episode in this season
                for ep in reversed(s.get("episodes", [])):
                    if ep.get("completed"):
                        last_watched = _format_episode(
                            s.get("number"), ep.get("number")
                        )
                        last_watch_date = _parse_date(ep.get("last_watched_at"))
                        break
                if last_watched:
                    break

        # Prefer the top-level last_watched_at when more specific date unavailable
        if not last_watch_date:
            last_watch_date = _parse_date(progress.get("last_watched_at"))

    # Next episode label
    next_ep = None
    next_premiere = None
    if next_ep_data:
        next_ep = _format_episode(
            next_ep_data.get("season"), next_ep_data.get("number")
        )
        next_premiere = _parse_date(next_ep_data.get("first_aired"))

    with db_session() as conn:
        service_id = _resolve_service_id(network, conn)

        existing = conn.execute(
            "SELECT id, is_abandoned, abandoned_override FROM shows WHERE trakt_id = ?",
            (trakt_id,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE shows SET
                    title                = ?,
                    status               = ?,
                    total_seasons        = ?,
                    current_season       = ?,
                    total_episodes       = ?,
                    last_watched_episode = ?,
                    next_episode         = ?,
                    next_premiere_date   = ?,
                    service_id           = ?,
                    last_watch_date      = ?
                WHERE trakt_id = ?
                """,
                (
                    title, show_status, total_seasons, current_season,
                    total_eps, last_watched, next_ep, next_premiere,
                    service_id, last_watch_date, trakt_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO shows (
                    trakt_id, title, status, total_seasons, current_season,
                    total_episodes, last_watched_episode, next_episode,
                    next_premiere_date, service_id, is_abandoned,
                    abandoned_override, last_watch_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    trakt_id, title, show_status, total_seasons, current_season,
                    total_eps, last_watched, next_ep, next_premiere,
                    service_id, last_watch_date,
                ),
            )


# ── Watchlist sync ─────────────────────────────────────────────────────────────

def sync_watchlist(client: TraktClient) -> int:
    """
    Pull the user's TV watchlist from Trakt and upsert into `watchlist` table.
    Returns the number of items processed.
    """
    logger.info("Fetching watchlist from Trakt...")
    items = client.get_watchlist()
    if not items:
        return 0

    count = 0
    for entry in items:
        show     = entry.get("show", {})
        trakt_id = str(show.get("ids", {}).get("trakt", ""))
        title    = show.get("title", "Unknown")

        if not trakt_id:
            continue

        network  = show.get("network", "")
        premiere = _parse_date(show.get("first_aired"))

        with db_session() as conn:
            service_id = _resolve_service_id(network, conn)
            existing = conn.execute(
                "SELECT id FROM watchlist WHERE trakt_id = ?", (trakt_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE watchlist SET title=?, service_id=?, premiere_date=? WHERE trakt_id=?",
                    (title, service_id, premiere, trakt_id),
                )
            else:
                conn.execute(
                    "INSERT INTO watchlist (trakt_id, title, service_id, premiere_date) VALUES (?,?,?,?)",
                    (trakt_id, title, service_id, premiere),
                )
        count += 1

    logger.info("Synced %d watchlist items.", count)
    return count


# ── Full sync entry point ──────────────────────────────────────────────────────

def run_full_sync(client: TraktClient) -> dict:
    """
    Convenience function called by the scheduler and the GUI sync button.
    Syncs watched shows and watchlist, then applies abandoned detection.
    """
    from core.abandoned import apply_abandoned_detection

    shows_count    = sync_watched_shows(client)
    watchlist_count = sync_watchlist(client)
    abandoned_count = apply_abandoned_detection()

    return {
        "shows":     shows_count,
        "watchlist": watchlist_count,
        "abandoned": abandoned_count,
    }
