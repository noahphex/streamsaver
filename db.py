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
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT    NOT NULL UNIQUE,
                trakt_network_id  TEXT,
                icon_path         TEXT,
                status            TEXT    NOT NULL DEFAULT 'not_subscribed'
                                          CHECK(status IN (
                                              'subscribed','suspended',
                                              'unsubscribed','not_subscribed'
                                          )),
                monthly_cost      REAL    DEFAULT 0.0,
                annual_cost       REAL    DEFAULT 0.0,
                next_billing_date TEXT
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
                ('networks_fetched_at',        ''),
                ('provider_country',           'US'),
                ('last_trakt_sync',            '');
        """)

        # Safe migrations for tables that already exist with older schemas
        _migrate_columns(conn, "services", [
            ("tmdb_provider_id", "INTEGER"),
        ])

    print(f"✅  Database ready: {DB_PATH}")


if __name__ == "__main__":
    init_db()
