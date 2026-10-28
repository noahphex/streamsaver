"""
core/show_tracker.py
Streamsaver — Show data sync: Trakt → SQLite
Author: Noah Lee

Incremental sync strategy:
  - First sync: pulls last 1 year of episode history
  - Subsequent syncs: pulls activity since last_trakt_sync, but never less than the
    30-day scoring window, so scores stay current; unseen shows decay to dormant
  - Groups episodes by show, computes interest scores from timestamps
  - Looks up TMDb providers for automatic show→service matching
  - Caps at 200 shows (most recently watched first)
  - Monthly archive pass prunes shows that are no longer relevant
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from db import db_session
from core.trakt_client import TraktClient
from core.tmdb_client import TMDbClient

logger = logging.getLogger(__name__)

# Interest scoring thresholds (from build_admin.txt spec)
_SCORE_ACTIVE     = 15
_SCORE_OCCASIONAL = 5

# _compute_interest_score only rewards watches within this many days
_SCORE_WINDOW_DAYS = 30

# Maximum shows to keep in hot storage
_MAX_SHOWS = 200

# Archive threshold: completed 2+ years ago
_ARCHIVE_AGE_DAYS = 730


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    """Parse a Trakt ISO datetime string to a timezone-aware datetime, or None."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_date_str(raw: Optional[str]) -> Optional[str]:
    """Return YYYY-MM-DD from a Trakt ISO datetime string, or None."""
    dt = _parse_dt(raw)
    return dt.strftime("%Y-%m-%d") if dt else None


def _compute_interest_score(watch_events: list[datetime]) -> int:
    """
    Compute an interest score for a show from a list of watched-at timestamps.

    Weighted signals per spec:
        +10  watched in last 7 days
        +5   watched in last 30 days
        +5   3+ episodes watched recently (binge signal)
        -10  no activity in 45+ days
    """
    now   = datetime.now(timezone.utc)
    score = 0

    recent_7  = [e for e in watch_events if (now - e).days <= 7]
    recent_30 = [e for e in watch_events if (now - e).days <= 30]

    if recent_7:
        score += 10
    if recent_30:
        score += 5
    if len(recent_30) >= 3:
        score += 5  # binge signal

    # No activity penalty
    if watch_events:
        most_recent = max(watch_events)
        if (now - most_recent).days >= 45:
            score -= 10

    return max(score, 0)


def _classify_state(score: int) -> str:
    """Map an interest score to a show_state label."""
    if score >= _SCORE_ACTIVE:
        return "active"
    if score >= _SCORE_OCCASIONAL:
        return "occasional"
    return "dormant"


def _get_setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )


# ── Main sync ──────────────────────────────────────────────────────────────────

def sync_watched_shows(
    client: TraktClient,
    tmdb_client: Optional[TMDbClient] = None,
    force_backfill: bool = False,
) -> int:
    """
    Incrementally sync watched shows from Trakt.

    Strategy:
      1. Read last_trakt_sync from settings.
      2. start_at = now - 1 year if never synced, else = last_trakt_sync.
      3. Pull episode history since start_at (grouped into show profiles).
      4. Compute interest_score and show_state for each show.
      5. Look up TMDb for primary streaming provider (if tmdb_client provided).
      6. Upsert compact show profiles (cap at _MAX_SHOWS most recent).
      7. Update last_trakt_sync.

    Returns the number of shows processed.
    """
    now = datetime.now(timezone.utc)

    with db_session() as conn:
        last_sync_str = _get_setting(conn, "last_trakt_sync")
        show_count = conn.execute("SELECT COUNT(*) AS count FROM shows").fetchone()["count"]

    last_sync_dt = _parse_dt(last_sync_str)
    if force_backfill:
        start_at = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logger.info("Forced backfill requested — pulling last 1 year of history (since %s)", start_at)
    elif show_count == 0:
        start_at = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logger.info("No local shows found — pulling last 1 year of history (since %s)", start_at)
    elif last_sync_dt and last_sync_dt > now:
        start_at = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logger.warning(
            "last_trakt_sync %s is in the future; using 1-year backfill since %s",
            last_sync_str,
            start_at,
        )
    elif last_sync_dt:
        # Scores only look back _SCORE_WINDOW_DAYS, so always re-read that window: every show
        # watched inside it gets a full rescore, and any show outside it decays to dormant.
        start_dt = min(last_sync_dt, now - timedelta(days=_SCORE_WINDOW_DAYS))
        start_at = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logger.info("Incremental sync since %s", start_at)
    else:
        start_at = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logger.info("First sync — pulling last 1 year of history (since %s)", start_at)

    logger.info("Fetching episode history from Trakt...")
    history = client.get_all_show_history(start_at=start_at)
    logger.info("Fetched %d Trakt show history event(s).", len(history))
    if not history:
        logger.info("No watch history in the sync window.")
        _decay_unseen_shows(set())
        _update_last_sync(now)
        return 0

    # Group episode events by show trakt_id
    show_map: dict[str, dict] = {}
    for event in history:
        show_stub = event.get("show", {})
        trakt_id  = str(show_stub.get("ids", {}).get("trakt", ""))
        if not trakt_id:
            continue

        if trakt_id not in show_map:
            show_map[trakt_id] = {
                "title":       show_stub.get("title", "Unknown"),
                "trakt_id":    trakt_id,
                "events":      [],
                "last_at_raw": None,
                "season":      0,
            }

        watched_at = _parse_dt(event.get("watched_at"))
        if watched_at:
            show_map[trakt_id]["events"].append(watched_at)

        # Track most recent watch date and last season seen
        ep = event.get("episode", {})
        ep_dt = _parse_dt(event.get("watched_at"))
        existing_last = _parse_dt(show_map[trakt_id]["last_at_raw"])
        if ep_dt and (existing_last is None or ep_dt > existing_last):
            show_map[trakt_id]["last_at_raw"] = event.get("watched_at")
            show_map[trakt_id]["season"] = ep.get("season", 0)

    # Sort by most recently watched, cap at _MAX_SHOWS
    sorted_shows = sorted(
        show_map.values(),
        key=lambda s: s["last_at_raw"] or "",
        reverse=True,
    )[:_MAX_SHOWS]

    logger.info("Processing %d unique shows from history...", len(sorted_shows))

    country = "US"
    with db_session() as conn:
        country = _get_setting(conn, "provider_country") or "US"

    count = 0
    for show_data in sorted_shows:
        try:
            _upsert_show(show_data, client, tmdb_client, country)
            count += 1
            # Throttle to avoid rate limits: 0.5s between shows with TMDb lookups
            if tmdb_client:
                time.sleep(0.5)
        except Exception as exc:
            logger.error("Error syncing show '%s': %s", show_data["title"], exc)

    _decay_unseen_shows(set(show_map))
    _update_last_sync(now)
    backfill_service_ids()
    logger.info("Synced %d shows.", count)
    return count


def _upsert_show(
    show_data: dict,
    client: TraktClient,
    tmdb_client: Optional[TMDbClient],
    country: str,
) -> None:
    """Compute score, look up providers, and upsert one show's compact profile."""
    trakt_id = show_data["trakt_id"]
    title    = show_data["title"]
    events   = show_data["events"]
    season   = show_data["season"]

    score      = _compute_interest_score(events)
    show_state = _classify_state(score)
    last_watch = _to_date_str(show_data["last_at_raw"])

    # Only fetch Trakt show summary for shows worth tracking closely
    trakt_status = None
    next_air_date = None
    if show_state in ("active", "occasional"):
        try:
            summary = client.get_show_summary(trakt_id) or {}
            trakt_status = summary.get("status")
            # Check for upcoming season/episode
            next_ep = client.get_next_episode(trakt_id)
            # "" (not None) when Trakt confirms no next episode, so the UPDATE's COALESCE
            # clears an old air date instead of keeping it forever.
            next_air_date = (_to_date_str(next_ep.get("first_aired")) if next_ep else None) or ""

            # Completed: show is definitively over
            if trakt_status in ("ended", "canceled"):
                show_state = "completed"
            # Completed: Trakt says "returning" but no next episode + not watched recently
            elif trakt_status == "returning series" and not next_air_date:
                last_dt = _parse_dt(show_data.get("last_at_raw"))
                days_since = (datetime.now(timezone.utc) - last_dt).days if last_dt else 999
                if days_since >= 30:
                    show_state = "completed"
            # Anticipated: returning show with a known air date
            elif trakt_status == "returning series" and next_air_date:
                show_state = "anticipated"

            # Boost score for returning show (only when still active)
            if trakt_status == "returning series" and show_state != "completed":
                score = min(score + 8, 100)
        except Exception as exc:
            logger.debug("Could not fetch Trakt summary for %s: %s", trakt_id, exc)

    # TMDb provider lookup
    tmdb_id = None
    primary_provider = None
    primary_provider_id = None
    service_id = None

    if tmdb_client:
        try:
            tmdb_id = tmdb_client.search_tv_show(title)
            if tmdb_id:
                providers = tmdb_client.get_show_providers(tmdb_id)
                if providers:
                    best = providers[0]
                    primary_provider    = best.get("provider_name")
                    primary_provider_id = best.get("provider_id")
                    # Match to a user-configured service
                    service_id = _resolve_service_id(primary_provider_id, primary_provider)
        except Exception as exc:
            logger.debug("TMDb lookup failed for '%s': %s", title, exc)

    with db_session() as conn:
        if service_id is None:
            service_id = _resolve_service_id_conn(primary_provider_id, primary_provider, conn)

        existing = conn.execute(
            "SELECT id, abandoned_override FROM shows WHERE trakt_id = ?",
            (trakt_id,),
        ).fetchone()

        # Respect manual abandoned override
        if existing and existing["abandoned_override"]:
            show_state = "abandoned"

        if existing:
            conn.execute(
                """
                UPDATE shows SET
                    tmdb_id             = COALESCE(?, tmdb_id),
                    title               = ?,
                    trakt_status        = COALESCE(?, trakt_status),
                    show_state          = ?,
                    interest_score      = ?,
                    last_season_seen    = ?,
                    last_watch_date     = ?,
                    next_air_date       = COALESCE(?, next_air_date),
                    primary_provider    = COALESCE(?, primary_provider),
                    primary_provider_id = COALESCE(?, primary_provider_id),
                    service_id          = COALESCE(?, service_id)
                WHERE trakt_id = ?
                """,
                (
                    tmdb_id, title, trakt_status, show_state, score,
                    season, last_watch, next_air_date,
                    primary_provider, primary_provider_id, service_id,
                    trakt_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO shows (
                    trakt_id, tmdb_id, title, trakt_status, show_state,
                    interest_score, last_season_seen, last_watch_date,
                    next_air_date, primary_provider, primary_provider_id,
                    abandoned_override, service_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    trakt_id, tmdb_id, title, trakt_status, show_state,
                    score, season, last_watch,
                    next_air_date, primary_provider, primary_provider_id,
                    service_id,
                ),
            )


def _resolve_service_id(
    provider_id: Optional[int], provider_name: Optional[str]
) -> Optional[int]:
    """Match a TMDb provider to a user-configured service (opens its own connection)."""
    if not provider_id and not provider_name:
        return None
    with db_session() as conn:
        return _resolve_service_id_conn(provider_id, provider_name, conn)


def _resolve_service_id_conn(
    provider_id: Optional[int], provider_name: Optional[str], conn
) -> Optional[int]:
    """Match a TMDb provider to a user-configured service using an existing connection."""
    if provider_id:
        # 1. Exact match by TMDb provider_id on the service itself
        row = conn.execute(
            "SELECT id FROM services WHERE tmdb_provider_id = ?", (provider_id,)
        ).fetchone()
        if row:
            return row["id"]
        # 2. Bundle match: provider is a member of a bundle service
        row = conn.execute(
            "SELECT service_id FROM service_bundles WHERE tmdb_provider_id = ?",
            (provider_id,),
        ).fetchone()
        if row:
            return row["service_id"]
    if provider_name:
        # 3. Name match on services
        row = conn.execute(
            "SELECT id FROM services WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
            (provider_name,),
        ).fetchone()
        if row:
            return row["id"]
        # 4. Bundle name match
        row = conn.execute(
            "SELECT service_id FROM service_bundles WHERE LOWER(TRIM(provider_name)) = LOWER(TRIM(?))",
            (provider_name,),
        ).fetchone()
        if row:
            return row["service_id"]
    return None


def backfill_service_ids() -> int:
    """
    Update service_id for any show where it is NULL but primary_provider_id/name
    can be resolved via the services or service_bundles tables.
    Run after every sync and after bundle config changes.
    Returns number of rows updated.
    """
    updated = 0
    with db_session() as conn:
        orphans = conn.execute(
            "SELECT id, primary_provider_id, primary_provider FROM shows WHERE service_id IS NULL"
        ).fetchall()
        for row in orphans:
            sid = _resolve_service_id_conn(row["primary_provider_id"], row["primary_provider"], conn)
            if sid:
                conn.execute("UPDATE shows SET service_id = ? WHERE id = ?", (sid, row["id"]))
                updated += 1
    if updated:
        logger.info("Backfilled service_id for %d shows.", updated)
    return updated


def _decay_unseen_shows(seen_ids: set[str]) -> int:
    """
    Drop shows not watched in the scoring window to dormant (score 0).

    Without this, a show's score froze at its last sync and it stayed "active" forever.
    Anticipated shows only decay once their air date has passed.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT trakt_id FROM shows
            WHERE abandoned_override = 0
              AND (show_state IN ('active', 'occasional')
                   OR (show_state = 'anticipated'
                       AND (next_air_date IS NULL OR next_air_date = '' OR next_air_date < ?)))
            """,
            (today,),
        ).fetchall()
        stale = [(r["trakt_id"],) for r in rows if r["trakt_id"] not in seen_ids]
        conn.executemany(
            "UPDATE shows SET interest_score = 0, show_state = 'dormant' WHERE trakt_id = ?",
            stale,
        )
    if stale:
        logger.info("Decayed %d show(s) not watched in the last %d days to dormant.", len(stale), _SCORE_WINDOW_DAYS)
    return len(stale)


def _update_last_sync(now: datetime) -> None:
    """Persist the current timestamp as last_trakt_sync."""
    with db_session() as conn:
        _set_setting(conn, "last_trakt_sync", now.strftime("%Y-%m-%dT%H:%M:%S.000Z"))


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

        premiere = _to_date_str(show.get("first_aired"))

        with db_session() as conn:
            # Try to match watchlist item to a service via the shows table
            show_row = conn.execute(
                "SELECT service_id FROM shows WHERE trakt_id = ?", (trakt_id,)
            ).fetchone()
            service_id = show_row["service_id"] if show_row else None

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


# ── Archive pass ───────────────────────────────────────────────────────────────

def run_archive_pass() -> int:
    """
    Archive shows that are no longer worth tracking in hot storage.

    A show is archived when ALL of the following are true:
      - last_watch_date is more than 2 years ago (or null)
      - trakt_status is ended or canceled (not returning)
      - next_air_date is null (no announced future season)
      - interest_score < 5 (dormant)
      - abandoned_override is not set

    Returns the count of shows archived.
    """
    from datetime import date
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_ARCHIVE_AGE_DAYS)).strftime("%Y-%m-%d")

    with db_session() as conn:
        result = conn.execute(
            """
            UPDATE shows
            SET show_state = 'archived'
            WHERE abandoned_override = 0
              AND show_state NOT IN ('active', 'occasional', 'anticipated', 'ignored', 'archived')
              AND interest_score < 5
              AND (next_air_date IS NULL OR next_air_date = '')
              AND (trakt_status IN ('ended', 'canceled') OR trakt_status IS NULL)
              AND (last_watch_date IS NULL OR last_watch_date < ?)
            """,
            (cutoff,),
        )
        count = result.rowcount

    if count:
        logger.info("Archive pass: %d show(s) archived.", count)
    return count


# ── Full sync entry point ──────────────────────────────────────────────────────

def run_full_sync(
    client: TraktClient,
    tmdb_client: Optional[TMDbClient] = None,
    force_backfill: bool = False,
) -> dict:
    """
    Entry point called by the scheduler and the GUI sync button.
    Runs incremental show sync, watchlist sync, and archive pass.
    """
    shows_count     = sync_watched_shows(client, tmdb_client, force_backfill=force_backfill)
    watchlist_count = sync_watchlist(client)
    archived_count  = run_archive_pass()

    return {
        "shows":     shows_count,
        "watchlist": watchlist_count,
        "archived":  archived_count,
    }
