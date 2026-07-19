"""
db.py
Streamsaver — SQLite database initialization and connection helper
Author: Noah Lee
"""

import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "streamsaver.db")


def get_connection() -> sqlite3.Connection:
    """Return a connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """Context manager that commits on success, rolls back on error."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_columns(conn, table: str, columns: list[tuple]) -> None:
    """Add columns to an existing table if they don't already exist."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_type in columns:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


def _shows_needs_migration(conn) -> bool:
    """Return True if the shows table is missing the new show_state column."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(shows)").fetchall()}
    return bool(cols) and "show_state" not in cols


def init_db() -> None:
    """Create all tables if they don't already exist, migrating schema as needed."""
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

    with db_session() as conn:
        # Migrate shows table to compact-profile schema if it's the old layout.
        # The old schema used is_abandoned; the new one uses show_state.
        if _shows_needs_migration(conn):
            conn.execute("DROP TABLE IF EXISTS shows")

        conn.executescript("""
            -- Streaming services
            CREATE TABLE IF NOT EXISTS services (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL UNIQUE,
                trakt_network_id    TEXT,
                icon_path           TEXT,
                status              TEXT    NOT NULL DEFAULT 'not_subscribed'
                                            CHECK(status IN (
                                                'subscribed','suspended',
                                                'unsubscribed','not_subscribed'
                                            )),
                monthly_cost        REAL    DEFAULT 0.0,
                annual_cost         REAL    DEFAULT 0.0,
                billing_cycle       TEXT    NOT NULL DEFAULT 'monthly',
                billing_anchor_date TEXT,
                auto_renew          INTEGER NOT NULL DEFAULT 1,
                next_billing_date   TEXT    -- legacy; prefer billing_anchor_date
            );

            -- TV shows — compact activity profiles (one row per show)
            CREATE TABLE IF NOT EXISTS shows (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trakt_id            TEXT    NOT NULL UNIQUE,
                tmdb_id             INTEGER,
                title               TEXT    NOT NULL,
                trakt_status        TEXT,               -- returning series/ended/canceled
                show_state          TEXT    NOT NULL DEFAULT 'dormant'
                                            CHECK(show_state IN (
                                                'active','occasional','dormant',
                                                'abandoned','anticipated',
                                                'ignored','archived'
                                            )),
                interest_score      INTEGER NOT NULL DEFAULT 0,
                last_season_seen    INTEGER DEFAULT 0,
                completion_percent  REAL    DEFAULT 0.0,
                last_watch_date     TEXT,               -- ISO date
                next_air_date       TEXT,               -- ISO date of next episode/season
                primary_provider    TEXT,               -- human-readable provider name
                primary_provider_id INTEGER,            -- TMDb provider_id
                abandoned_override  BOOLEAN NOT NULL DEFAULT 0,
                service_id          INTEGER REFERENCES services(id)
                                            ON DELETE SET NULL
            );

            -- TV show watchlist (shows user wants to watch, not yet started)
            CREATE TABLE IF NOT EXISTS watchlist (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                trakt_id       TEXT    NOT NULL UNIQUE,
                title          TEXT    NOT NULL,
                service_id     INTEGER REFERENCES services(id)
                                       ON DELETE SET NULL,
                premiere_date  TEXT
            );

            -- Cached streaming provider list (from TMDb)
            CREATE TABLE IF NOT EXISTS networks (
                name        TEXT PRIMARY KEY,
                country     TEXT,
                fetched_at  TEXT,
                provider_id INTEGER,
                logo_path   TEXT
            );

            -- Bundle provider mappings: extra TMDb providers that belong to a service
            -- (e.g. Disney Plus + ESPN+ both map to the user's Hulu subscription)
            CREATE TABLE IF NOT EXISTS service_bundles (
                service_id        INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                tmdb_provider_id  INTEGER NOT NULL,
                provider_name     TEXT,
                PRIMARY KEY (service_id, tmdb_provider_id)
            );

            -- Key/value settings store
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- Seed default settings (ignored if already present)
            INSERT OR IGNORE INTO settings (key, value) VALUES
                ('notification_email',        ''),
                ('abandoned_mode',            'manual'),
                ('abandoned_threshold_days',  '90'),
                ('status_alert_frequency',    'weekly'),
                ('last_status_alert_sent',     ''),
                ('discord_webhook_url',        ''),
                ('networks_fetched_at',        ''),
                ('provider_country',           'US'),
                ('last_trakt_sync',            '');
        """)

        # Safe migrations for tables that already exist with older schemas
        _migrate_columns(conn, "services", [
            ("tmdb_provider_id",    "INTEGER"),
            ("billing_cycle",       "TEXT NOT NULL DEFAULT 'monthly'"),
            ("billing_anchor_date", "TEXT"),
            ("auto_renew",          "INTEGER NOT NULL DEFAULT 1"),
            ("icon_last_updated",   "TEXT"),
        ])

        # Rebuild shows table if it's missing the 'completed' state in its CHECK constraint
        _migrate_shows_check_constraint(conn)

    _migrate_billing_data()
    _seed_default_bundles()
    print(f"✅  Database ready: {DB_PATH}")


# ── Schema migrations ───────────────────────────────────────────────────────────

def _migrate_shows_check_constraint(conn) -> None:
    """
    Rebuild the shows table if it's missing 'completed' from the show_state
    CHECK constraint. SQLite doesn't support ALTER COLUMN, so we use the
    rename-copy-drop pattern. Safe to call repeatedly.
    """
    # Detect old constraint by attempting a test insert
    try:
        conn.execute(
            "INSERT INTO shows (trakt_id, title, show_state) VALUES ('__chk__', '__chk__', 'completed')"
        )
        conn.execute("DELETE FROM shows WHERE trakt_id = '__chk__'")
        return  # already supports 'completed'
    except Exception:
        pass  # needs migration

    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        CREATE TABLE IF NOT EXISTS shows_new (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            trakt_id            TEXT    NOT NULL UNIQUE,
            tmdb_id             INTEGER,
            title               TEXT    NOT NULL,
            trakt_status        TEXT,
            show_state          TEXT    NOT NULL DEFAULT 'dormant'
                                        CHECK(show_state IN (
                                            'active','occasional','dormant','completed',
                                            'abandoned','anticipated',
                                            'ignored','archived'
                                        )),
            interest_score      INTEGER NOT NULL DEFAULT 0,
            last_season_seen    INTEGER DEFAULT 0,
            completion_percent  REAL    DEFAULT 0.0,
            last_watch_date     TEXT,
            next_air_date       TEXT,
            primary_provider    TEXT,
            primary_provider_id INTEGER,
            abandoned_override  BOOLEAN NOT NULL DEFAULT 0,
            service_id          INTEGER REFERENCES services(id) ON DELETE SET NULL
        );

        INSERT INTO shows_new SELECT * FROM shows;
        DROP TABLE shows;
        ALTER TABLE shows_new RENAME TO shows;

        PRAGMA foreign_keys = ON;
    """)


def _migrate_billing_data() -> None:
    """
    One-time migration: auto-detect billing_cycle from cost fields and clear
    fake annual_cost values (monthly * 12 approximations) for monthly services.
    Sets billing_anchor_date = next_billing_date for all services as initial anchor.
    Safe to run repeatedly.
    """
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, monthly_cost, annual_cost, next_billing_date, billing_cycle FROM services"
        ).fetchall()

        for r in rows:
            mc = r["monthly_cost"] or 0.0
            ac = r["annual_cost"] or 0.0
            cycle = r["billing_cycle"]
            anchor = r["next_billing_date"]

            # Auto-detect cycle if still at default
            if cycle == "monthly" and ac > 0 and mc == 0:
                # Only annual cost set → this is an annual plan
                cycle = "annual"
            elif cycle == "monthly" and ac > 0 and mc > 0:
                # Both set → monthly plan with fake derived annual; clear annual
                ac = 0.0

            conn.execute(
                """UPDATE services
                   SET billing_cycle = ?,
                       annual_cost = ?,
                       billing_anchor_date = COALESCE(billing_anchor_date, ?)
                   WHERE id = ?""",
                (cycle, ac, anchor, r["id"]),
            )


# ── Default bundle seeds ────────────────────────────────────────────────────────

# Well-known provider bundles: (service_name_fragment, [(tmdb_provider_id, provider_name)])
# service_name_fragment is matched case-insensitively against services.name.
# These only insert if the service exists; safe to run multiple times (INSERT OR IGNORE).
_DEFAULT_BUNDLES: list[tuple[str, list[tuple[int, str]]]] = [
    # Disney Bundle (Hulu subscription includes Disney+ and ESPN+)
    ("hulu", [
        (337,  "Disney Plus"),
        (508,  "DisneyNOW"),
    ]),
    # Amazon Prime Video variants
    ("amazon prime", [
        (9,    "Amazon Prime Video"),
        (10,   "Amazon Video"),
        (613,  "Amazon Prime Video Free with Ads"),
    ]),
    # Add-on channels billed through Prime (match by service name)
    ("shudder", [
        (99,   "Shudder"),
        (204,  "Shudder Amazon Channel"),
    ]),
    ("paramount+", [
        (582,  "Paramount+ Amazon Channel"),
        (2616, "Paramount Plus Essential"),
        (2303, "Paramount Plus Premium"),
    ]),
    ("mgm+", [
        (34,   "MGM Plus"),
        (583,  "MGM+ Amazon Channel"),
    ]),
    ("amc+", [
        (526,  "AMC+"),
        (528,  "AMC+ Amazon Channel"),
    ]),
    ("starz", [
        (43,   "Starz"),
        (1794, "Starz Amazon Channel"),
    ]),
    # HBO / Max — rebranded, multiple provider IDs in TMDb
    ("hbo", [
        (1899, "HBO Max"),
        (1825, "HBO Max Amazon Channel"),
    ]),
]


def _seed_default_bundles() -> None:
    """
    Insert well-known bundle mappings for any matching services.
    Safe to call on every startup — uses INSERT OR IGNORE.
    """
    with db_session() as conn:
        for name_fragment, providers in _DEFAULT_BUNDLES:
            row = conn.execute(
                "SELECT id FROM services WHERE LOWER(name) LIKE ?",
                (f"%{name_fragment}%",),
            ).fetchone()
            if not row:
                continue
            service_id = row["id"]
            for pid, pname in providers:
                conn.execute(
                    """INSERT OR IGNORE INTO service_bundles
                       (service_id, tmdb_provider_id, provider_name)
                       VALUES (?, ?, ?)""",
                    (service_id, pid, pname),
                )


if __name__ == "__main__":
    init_db()
