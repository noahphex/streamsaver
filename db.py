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


def init_db() -> None:
    """Create all tables if they don't already exist."""
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

    with db_session() as conn:
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

            -- TV shows sourced from Trakt watch history
            CREATE TABLE IF NOT EXISTS shows (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                trakt_id              TEXT    NOT NULL UNIQUE,
                title                 TEXT    NOT NULL,
                status                TEXT,
                total_seasons         INTEGER DEFAULT 0,
                current_season        INTEGER DEFAULT 0,
                total_episodes        INTEGER DEFAULT 0,
                last_watched_episode  TEXT,
                next_episode          TEXT,
                next_premiere_date    TEXT,
                service_id            INTEGER REFERENCES services(id)
                                              ON DELETE SET NULL,
                is_abandoned          BOOLEAN NOT NULL DEFAULT 0,
                abandoned_override    BOOLEAN NOT NULL DEFAULT 0,
                last_watch_date       TEXT
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

            -- Key/value settings store
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- Seed default settings (ignored if already present)
            INSERT OR IGNORE INTO settings (key, value) VALUES
                ('notification_email',        ''),
                ('abandoned_mode',            'manual'),
                ('abandoned_threshold_days',  '90');
        """)

    print(f"✅  Database ready: {DB_PATH}")


if __name__ == "__main__":
    init_db()
