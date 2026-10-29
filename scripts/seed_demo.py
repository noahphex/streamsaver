"""Create disposable, offline screenshot data. Demo credentials unlock with 'demo'."""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
import setup_credentials as credentials


def seed_demo(data_dir: Path, config_dir: Path, force: bool = False) -> None:
    data_dir, config_dir = Path(data_dir), Path(config_dir)
    database = data_dir / "streamsaver.db"
    cred_file = config_dir / "credentials.enc"
    if not force and (database.exists() or cred_file.exists() or (config_dir / ".salt").exists()):
        raise FileExistsError("Database or credentials already exist; use empty directories or --force.")
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    old_paths = db.DB_PATH, credentials.CONFIG_DIR, credentials.CRED_FILE, credentials.SALT_FILE
    try:
        db.DB_PATH = str(database)
        credentials.CONFIG_DIR = str(config_dir)
        credentials.CRED_FILE = str(cred_file)
        credentials.SALT_FILE = str(config_dir / ".salt")
        for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm"), cred_file, config_dir / ".salt"):
            path.unlink(missing_ok=True)
        db.init_db()
        credentials.encrypt_credentials({
            "trakt_client_id": "demo-trakt-client",
            "trakt_client_secret": "demo-trakt-secret",
            "trakt_username": "demo-viewer",
            "trakt_access_token": "demo-access-token",
            "trakt_refresh_token": "demo-refresh-token",
            "tmdb_api_key": "demo-tmdb-key",
            "gmail_sender": "demo@example.com",
            "gmail_app_password": "demo-app-password",
            "notify_email": "",
        }, "demo", quiet=True)
        today = date.today()
        day = lambda offset: (today + timedelta(days=offset)).isoformat()
        services = [
            ("Netflix", "subscribed", 17.99, 0, "monthly", 8),
            ("Hulu", "subscribed", 9.99, 0, "monthly", 12),
            ("Max", "suspended", 16.99, 0, "monthly", 20),
            ("Peacock", "unsubscribed", 7.99, 0, "monthly", 25),
            ("Prime Video", "subscribed", 0, 139.00, "annual", 180),
            ("Apple TV+", "suspended", 9.99, 0, "monthly", 18),
            ("Paramount+", "subscribed", 0, 59.99, "annual", 40),
        ]
        # Titles are invented; no metadata, sync, or provider-download code runs here.
        shows = [
            ("The Lantern Bureau", "Netflix", "active", 24, 2),
            ("Orbit of Paper", "Netflix", "active", 22, 9),
            ("Velvet Signal", "Netflix", "anticipated", 19, 22),
            ("Harbor of Clocks", "Netflix", "anticipated", 17, 45),
            ("The Glass Orchard", "Netflix", "completed", 0, None),
            ("Postcards from Ember", "Hulu", "dormant", 0, None),
            ("The Quiet Compass", "Hulu", "abandoned", 0, None),
            ("Maple Station Nine", "Hulu", "completed", 0, None),
            ("The Copper Weather", "Max", "anticipated", 23, 3),
            ("Midnight Cartographers", "Max", "anticipated", 21, 14),
            ("A Pocket of Thunder", "Max", "dormant", 0, None),
            ("The Borrowed Horizon", "Peacock", "anticipated", 18, 27),
            ("Clouds over Cinder Bay", "Peacock", "archived", 0, None),
            ("The Marble Dispatch", "Prime Video", "active", 25, 5),
            ("Twelve Small Moons", "Prime Video", "occasional", 12, 18),
            ("The Last Apricot", "Prime Video", "anticipated", 19, 38),
            ("Letters to the Tide", "Prime Video", "completed", 0, None),
            ("The Invisible Platform", "Apple TV+", "anticipated", 20, 52),
            ("Foxglove Frequency", "Apple TV+", "dormant", 0, None),
            ("Cobalt Afternoon", "Paramount+", "active", 23, 11),
            ("The Seventh Teacup", "Paramount+", "anticipated", 18, 29),
            ("A Season of Kites", "Paramount+", "occasional", 10, None),
            ("The Unmapped Room", None, "anticipated", 20, 7),
            ("Silver in the Attic", None, "dormant", 0, None),
            ("The Clockwork Meadow", None, "active", 21, None),
        ]
        with db.db_session() as conn:
            ids = {}
            for name, status, monthly, annual, cycle, billing in services:
                cursor = conn.execute(
                    "INSERT INTO services (name, status, monthly_cost, annual_cost, billing_cycle, billing_anchor_date) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (name, status, monthly, annual, cycle, day(billing)),
                )
                ids[name] = cursor.lastrowid
            for index, (title, service, state, score, upcoming) in enumerate(shows, 1):
                conn.execute(
                    "INSERT INTO shows (trakt_id, title, trakt_status, show_state, interest_score, "
                    "last_season_seen, completion_percent, last_watch_date, next_air_date, primary_provider, service_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"demo-{index}", title, "ended" if state == "completed" else "returning series", state,
                     score, 2, 100 if state == "completed" else 65, day(-3 if state == "active" else -90),
                     day(upcoming) if upcoming is not None else None, service, ids.get(service)),
                )
            conn.execute("UPDATE settings SET value=? WHERE key='last_trakt_sync'", (datetime.now().isoformat(timespec="seconds"),))
        print("Demo ready. Sign in with passphrase: demo")
    finally:
        db.DB_PATH, credentials.CONFIG_DIR, credentials.CRED_FILE, credentials.SALT_FILE = old_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="Replace existing database and credentials.")
    args = parser.parse_args()
    try:
        seed_demo(args.data_dir, args.config_dir, args.force)
    except FileExistsError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
