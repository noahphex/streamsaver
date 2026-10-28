<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logos/streamsaver_header_transparent.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/logos/streamsaver_header.jpg">
  <img alt="Streamsaver - Streaming Cost Intelligence" src="assets/logos/streamsaver_header.jpg" width="480">
</picture>

# Streamsaver

**Stack:** Python 3.11, Flask, Gunicorn, SQLite, Docker, Trakt API, TMDb API, Gmail SMTP  
**Current app:** responsive web UI on port `8092` plus a daily scheduler container

Streamsaver helps decide which streaming services are worth keeping. It imports your Trakt watched history and watchlist, looks up where shows are available through TMDb provider data, compares that against the services you track, and sends email recommendations about what to keep, suspend, renew, or revisit.

## How It Works

1. Sync your watched shows and watchlist from Trakt.
2. Use TMDb provider data to identify where shows are available.
3. Compare that show activity against your configured streaming services and costs.
4. Score shows based on recent watching, upcoming episodes, dormant/ignored/abandoned state, and watchlist interest.
5. Generate practical recommendations for subscriptions and optional email reports.

The score is a decision aid, not an automatic cancellation system. Streamsaver shows the evidence; you still decide what to change.

## Current Features

| Page | Purpose |
|---|---|
| Dashboard | Summary metrics, sync shortcuts, and recommendation counts. |
| Services | Add, edit, and delete streaming services. Track subscribed, suspended, unsubscribed, and not subscribed services. |
| Shows | Review synced shows, filter by state, sort by column, and mark shows as dormant, ignored, or abandoned. |
| Settings | Configure report email, provider country, abandoned-show behavior, sync/backfill Trakt, refresh providers, send reports, update credentials, and backup/restore. |
| About | Plain-English explanation of Streamsaver's purpose and methodology. |

## Docker Install

From this repository:

```sh
cd /Volumes/Media/homelab/streamsaver
docker compose up -d --build
```

The container exposes the web UI on port `8092`.

Local URLs used in this homelab:

- Caddy URL: `https://streamsaver.home.arpa`
- Direct URL: `http://192.168.1.156:8092`
- Localhost from the Docker host: `http://127.0.0.1:8092`

Verify:

```sh
docker compose ps
curl -f http://127.0.0.1:8092/health
curl -k -f https://streamsaver.home.arpa/health
```

`docker compose ps` should show both `streamsaver` and `streamsaver-scheduler`.
The scheduler runs once per day at `STREAMSAVER_SCHEDULER_RUN_AT` from `.env`
and uses `STREAMSAVER_PASSPHRASE` to decrypt credentials without an interactive prompt.

### Docker Volumes

The compose file mounts local folders into the container:

| Host path | Container path | Purpose |
|---|---|---|
| `./config` | `/app/config` | Encrypted credentials and salt. |
| `./data` | `/app/data` | SQLite database and logs. |
| `./assets/logos` | `/app/assets/logos` | Cached provider logos. |
| `./backups` | `/app/backups` | Backup exports and imports. |

The Docker command intentionally runs Gunicorn with one worker and multiple threads. Streamsaver keeps decrypted credentials in Flask memory after unlock, so one worker avoids unlock state drifting between processes.

## First-Run Setup

Open the web UI and go to `/setup`, or click **Run the setup wizard** from the unlock page if no credentials exist.

You will need:

- Trakt API app credentials from `https://trakt.tv/oauth/applications`
- Trakt redirect URI set to `urn:ietf:wg:oauth:2.0:oob`
- TMDb read-only API key from `https://www.themoviedb.org/settings/api`
- Gmail sender address and Gmail App Password from `https://myaccount.google.com/apppasswords`

The web setup wizard:

1. Creates a local encryption passphrase.
2. Builds the Trakt authorization URL from your Client ID.
3. Exchanges the Trakt PIN for access and refresh tokens, or, if the PIN is left blank,
   sends you to **Settings -> Update Credentials** to sign in with a device code.
4. Encrypts all credentials into `config/credentials.enc`.
5. Stores the encryption salt in `config/.salt`.
6. Unlocks the current web session after saving.

To update existing credentials from the web UI, unlock Streamsaver first, then go to **Settings -> Update Credentials**.

### CLI Setup Alternative

The original terminal wizard still works:

```sh
./run_streamsaver.sh --setup
```

For Docker-first installs, the web setup wizard is usually simpler because it writes to the mounted `./config` folder used by the container.

## Daily Use

1. Unlock Streamsaver with the passphrase.
2. Go to **Settings -> Sync Trakt Now**.
3. Use **Backfill Last Year** if you need to repopulate watched data after a reset or sync issue.
4. Use **Refresh Provider List** after changing provider country or when TMDb provider data looks stale.
5. Add or update subscriptions on **Services**.
6. Review sortable show data on **Shows**.
7. Run **Analysis & Send Report** to email current recommendations.

If `STREAMSAVER_PASSPHRASE` is set in `.env` or the compose environment, the app can unlock automatically on startup.

## Email Reports

Streamsaver can send Gmail SMTP reports for:

- Services to consider suspending.
- Annual renewals to review.
- Annual renewals likely worth keeping.
- Suspended services that may be worth resubscribing to.

Email logos are embedded as CID attachments using locally cached provider logos, so the email does not need to fetch external images when rendered.

## Discord Alerts

Streamsaver can send operational alerts to a Discord channel when the scheduled job fails.

Configure this from **Settings -> Discord Webhook URL**, then use **Test Discord Alert**.
The saved webhook is used for:

- Scheduler credential/decryption failures.
- Scheduled Trakt sync failures, including expired or invalid Trakt authorization.
- Scheduled analysis failures.
- Scheduled email digest delivery failures.

If Trakt authorization fails, open **Settings -> Update Credentials**, click **Get Trakt Code**, enter the code at trakt.tv/activate, then click **Finish Trakt Sign-In**. The nightly job picks up the new tokens on its next run. The PIN form below it is a fallback for changing the Client ID or Secret.

## Scheduler

Docker Compose starts `streamsaver-scheduler` automatically. It runs the daily sync/report pipeline at `STREAMSAVER_SCHEDULER_RUN_AT` from `.env`, defaulting to `02:00`.

You can still run the scheduler manually for testing:

```sh
./run_streamsaver.sh --scheduler
.venv/bin/python scheduler.py --run-once
.venv/bin/python scheduler.py
.venv/bin/python scheduler.py --run-at 08:00
```

Passphrase sources, in order:

1. `.env` file in the project root:
   ```sh
   STREAMSAVER_PASSPHRASE=your_passphrase
   ```
2. Existing environment variable.
3. Scheduler CLI argument: `--passphrase your_passphrase`.
4. Interactive prompt.

Example cron:

```cron
0 2 * * * STREAMSAVER_PASSPHRASE=yourpass /path/to/streamsaver/.venv/bin/python /path/to/streamsaver/scheduler.py --run-once
```

## Backup And Restore

Use **Settings -> Export Backup** to download a JSON backup. Use **Settings -> Restore Backup** to import one.

Restore replaces service and setting data from the backup. Provider logos can be refreshed afterward with **Refresh Provider List** if needed.

## Local Development

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open `http://localhost:8092`.

The legacy Tkinter GUI code is still present under `gui/`, but the maintained app path is the Flask web UI in `web/` served through `wsgi.py` and Docker.

## Project Structure

```text
streamsaver/
├── Dockerfile
├── docker-compose.yml
├── wsgi.py                         # Gunicorn entry point
├── main.py                         # Local Flask dev entry point
├── scheduler.py                    # Scheduled sync/report runner
├── setup_credentials.py            # CLI credential setup and encryption helpers
├── db.py                           # SQLite schema and migrations
├── web/
│   ├── app.py                      # Flask routes and web actions
│   ├── static/app.css              # Responsive web styling
│   └── templates/                  # Web UI templates
├── core/
│   ├── trakt_client.py             # Trakt REST API wrapper
│   ├── tmdb_client.py              # TMDb API wrapper
│   ├── show_tracker.py             # Sync, scoring, and show-state logic
│   ├── service_manager.py          # Service CRUD, providers, and logo cache
│   ├── backup.py                   # JSON backup import/export
│   └── analysis.py                 # Recommendation engine
├── email_reports/
│   ├── logo.py                     # CID logo attachments
│   ├── report_builder.py           # HTML email rendering
│   └── mailer.py                   # Gmail SMTP sender
├── assets/logos/                   # Cached TMDb logos
├── config/                         # credentials.enc and .salt
├── data/                           # SQLite database and logs
└── backups/                        # Backup files
```

## Security Notes

- Credentials are encrypted with Fernet and PBKDF2.
- Encrypted credentials are stored in `config/credentials.enc`.
- The encryption salt is stored in `config/.salt`.
- `config/`, `data/`, and generated logo/cache files are gitignored.
- The passphrase cannot be recovered if lost.
- Unlocking stores decrypted credentials only in the running Flask process memory.
