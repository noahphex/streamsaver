<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logos/streamsaver_header_transparent.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/logos/streamsaver_header.jpg">
  <img alt="Streamsaver — Streaming Cost Intelligence" src="assets/logos/streamsaver_header.jpg" width="480">
</picture>

# Streamsaver

**Stack:** Python 3 · Tkinter · SQLite · Gmail SMTP · Trakt API · TMDb API  
**Status:** v1.0 — stable

Smart streaming cost management — tracks your Trakt.tv watch history, scores shows by recent activity, maps them to streaming services via TMDb, and sends you daily email recommendations on when to suspend, resubscribe, or skip an annual renewal.

---

## What it does

- **Interest scoring** — Weights your watch history (+10 watched last 7 days, +5 last 30 days, binge bonus, dormancy penalty) to classify each show as Active, Occasional, Dormant, Anticipated, or Archived
- **Show → Service matching** — Automatically maps each show to its streaming provider via TMDb's flatrate provider data
- **Suspend recommendations** — Flags subscribed services with no active shows and no upcoming premieres before the next billing date
- **Annual billing analysis** — Detects low-engagement services approaching an annual renewal and recommends skipping; flags active services worth renewing
- **Resubscribe alerts** — Detects anticipated shows with high interest scores returning on services you've suspended
- **Incremental sync** — Only pulls watch history since the last sync (first sync: last 1 year); caps at 200 shows
- **Email reports** — Dracula-themed HTML digest emails sent via Gmail SMTP with CID-embedded service logos (compatible with Gmail, Apple Mail, and Outlook)

---

## Setup

### 1. Prerequisites

- Python 3.11+ with Tkinter  
  *macOS Homebrew:* Tkinter must match your Python version. Run the following to install the right one automatically:
  ```bash
  brew install python-tk@$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  ```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Shortcut:** `run_streamsaver.sh` will create the venv and install dependencies automatically on first run if they're missing.

### 3. Configure credentials

Run the setup wizard once to securely encrypt your API keys and Gmail App Password:

```bash
./run_streamsaver.sh --setup
```

You will need:
- **Trakt API credentials** — create an app at https://trakt.tv/oauth/applications  
  The wizard will open a browser for PIN-based OAuth — no manual token copying needed
- **TMDb read-only API key** — create at https://www.themoviedb.org/settings/api
- **Gmail App Password** — generate at https://myaccount.google.com/apppasswords

### 4. Launch the Web UI

```bash
python main.py
```

Open `http://localhost:8092` and enter your passphrase when prompted to unlock credentials.

**First launch:** The app will automatically fetch the streaming provider list from TMDb. Go to **Settings → Refresh Provider List** if you need to refresh it.

### 5. Add your services

Go to the **Services** tab, click **Add Service**, and pick from the TMDb provider dropdown. Enter your monthly/annual cost and next billing date.

Service logos are downloaded automatically from TMDb and cached locally in `assets/logos/`. Logos are refreshed automatically every 30 days. No external image fetches happen during email rendering.

### 6. Sync and analyse

In **Settings**, click **🔄 Sync Trakt Now** to pull your watch history. Then click **📧 Run Analysis & Send Report** to get suspend/resubscribe recommendations.

### Backup restore behavior

If you restore from **Settings → Restore Backup**, Streamsaver now automatically backfills `icon_path` values for restored services right after import. This repopulates local logo files and ensures email logos render without needing a separate manual **Refresh Provider List** step.

---

## Scheduler

The scheduler runs a daily sync + analysis + email pipeline automatically.

```bash
# Run once immediately (via launch script)
./run_streamsaver.sh --scheduler

# Or run directly for more options
.venv/bin/python scheduler.py --run-once   # run once
.venv/bin/python scheduler.py              # continuous (2:00 AM daily)
.venv/bin/python scheduler.py --run-at 08:00  # custom time
```

**Passphrase options (in priority order):**
1. `.env` file in the project root — `run_streamsaver.sh` sources this automatically:
   ```bash
   # .env  (never committed — listed in .gitignore)
   STREAMSAVER_PASSPHRASE=your_passphrase
   ```
2. Environment variable already set in your shell: `STREAMSAVER_PASSPHRASE=your_passphrase`
3. CLI argument (scheduler only): `--passphrase your_passphrase`
4. Interactive prompt

### Cron (macOS/Linux)

```cron
0 2 * * * STREAMSAVER_PASSPHRASE=yourpass /path/to/streamsaver/.venv/bin/python /path/to/streamsaver/scheduler.py --run-once
```

---

## Features

| Feature | Description |
|---|---|
| **Services tab** | Add/edit/delete streaming services with TMDb provider picker. Set status: Subscribed, Suspended, Unsubscribed, Not Subscribed. Enter monthly/annual cost and billing date. |
| **Shows tab** | View all synced shows with interest score, show state (color-coded), primary streaming provider, last watched date, and next air date. Filter by state. Manually mark shows as abandoned. |

## Homelab Docker

This copy runs as a responsive Flask web app instead of the retired Tkinter GUI.

URLs:

- Caddy URL: `https://streamsaver.home.arpa`
- Direct URL: `http://192.168.1.156:8092`

Files:

- Compose file: `/Volumes/Media/homelab/streamsaver/docker-compose.yml`
- Encrypted credentials: `/Volumes/Media/homelab/streamsaver/config`
- SQLite data/logs: `/Volumes/Media/homelab/streamsaver/data`
- Provider logos: `/Volumes/Media/homelab/streamsaver/assets/logos`
- Backups: `/Volumes/Media/homelab/streamsaver/backups`

Run:

```sh
cd /Volumes/Media/homelab/streamsaver
docker compose up -d --build
```

Verify:

```sh
docker compose ps
curl -f http://192.168.1.156:8092/health
curl -k -f https://streamsaver.home.arpa/health
```
| **Settings tab** | Set notification email. Configure country for provider data. Sync Trakt, refresh providers, and run analysis + email on demand. Displays last sync timestamp. |
| **Email digest** | Single daily email covering all recommendation types — suspend, consider non-renewal, likely worth renewing, and resubscribe. Service logos embedded as CID attachments (no external URLs). Includes a summary metrics row (monthly spend, potential savings, services analyzed) and per-card confidence scores. |
| **Suspend** | Monthly services with no active or upcoming content flagged before billing date. Shows monthly + annual savings if paused. Top recommendation in each section is highlighted. |
| **Consider Non-Renewal** | Annual services nearing renewal with low engagement. Highlights that access continues through the paid period. |
| **Likely Worth Renewing** | Annual services nearing renewal with active engagement — confirms the renewal is justified. |
| **Resubscribe** | Previously suspended services with returning high-interest content — lists upcoming show titles and air dates. |
| **Recommendation confidence** | Each card shows a `Confidence: High/Medium/Low` signal derived from watch score and active show count. |
| **Logo TTL refresh** | Service logos are cached locally and automatically refreshed from TMDb every 30 days. |

---

## Show States

| State | Meaning | Score |
|---|---|---|
| 🟢 Active | Currently watching | 15+ |
| 🔵 Occasional | Watching but not binging | 5–14 |
| ⚫ Dormant | Haven't watched in a while | < 5 |
| 🟡 Anticipated | Upcoming new season, previously engaged | — |
| 🔴 Abandoned | Manually marked or auto-detected | — |
| 🗄 Archived | Ended 2+ years ago, low score, no future season | — |

---

## Project structure

```
streamsaver/
├── main.py                        # GUI entry point
├── scheduler.py                   # Daily scheduled runner
├── setup_credentials.py           # Credential setup wizard (PIN OAuth)
├── db.py                          # SQLite init, schema, migrations
├── requirements.txt
├── assets/
│   └── logos/                     # Cached TMDb provider logos (PNG/JPEG, gitignored)
├── config/                        # credentials.enc + .salt (auto-created, gitignored)
├── data/                          # streamsaver.db + logs (auto-created, gitignored)
├── core/
│   ├── trakt_client.py            # Trakt REST API wrapper
│   ├── tmdb_client.py             # TMDb API wrapper (providers + show lookup)
│   ├── show_tracker.py            # Incremental sync, interest scoring, archive pass
│   ├── service_manager.py         # Service CRUD, provider cache, logo downloader
│   └── analysis.py                # Suspend / resubscribe recommendation engine
├── gui/
│   ├── app.py                     # Main window + credential unlock
│   ├── theme.py                   # Dracula colour palette
│   ├── widgets.py                 # Shared themed widgets + ttk styles
│   ├── services_page.py           # Services tab
│   ├── shows_page.py              # Shows tab (state badges, scores)
│   └── settings_page.py           # Settings tab (sync, analysis, email)
└── email_reports/
    ├── logo.py                    # Centralized logo helper (CID attachment renderer)
    ├── report_builder.py          # HTML report + digest builder
    ├── mailer.py                  # Gmail SMTP sender (multipart/related)
    └── templates/base.html        # Dracula email shell template
```

---

## Security notes

- Credentials are encrypted with Fernet + PBKDF2 and stored in `config/credentials.enc`
- The encryption salt is in `config/.salt`
- **Both files are gitignored** — never committed to the repository
- The passphrase is never stored; it is entered at startup each time
