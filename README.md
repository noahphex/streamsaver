# Streamsaver

**Author:** Noah Lee  
**Stack:** Python 3 · Tkinter · SQLite · Gmail SMTP · Trakt API

Smart streaming cost management — tracks your Trakt.tv watch history and tells you when to suspend or resubscribe to streaming services.

---

## Setup

### 1. Prerequisites

- Python 3.11+ with Tkinter  
  *macOS Homebrew:* `brew install python-tk@3.14`

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure credentials

Run the setup wizard once to securely encrypt your Trakt API keys and Gmail App Password:

```bash
python setup_credentials.py
```

You will need:
- **Trakt API credentials** — create an app at https://trakt.tv/oauth/applications
- **Gmail App Password** — generate at https://myaccount.google.com/apppasswords

### 4. Launch the GUI

```bash
python main.py
```

Enter your passphrase when prompted to unlock credentials.

---

## Scheduler

The scheduler runs a daily sync + analysis + email pipeline.

```bash
# Run once immediately
python scheduler.py --run-once

# Run continuously (schedule at 2:00 AM daily)
python scheduler.py

# Custom time
python scheduler.py --run-at 08:00
```

**Passphrase options (in priority order):**
1. Environment variable: `STREAMSAVER_PASSPHRASE=your_passphrase`
2. CLI argument: `--passphrase your_passphrase`
3. Interactive prompt

### Cron (macOS/Linux)

```cron
0 2 * * * STREAMSAVER_PASSPHRASE=yourpass /path/to/streamsaver/.venv/bin/python /path/to/streamsaver/scheduler.py --run-once
```

### Task Scheduler (Windows)

Set the action to:
```
Program: C:\path\to\streamsaver\.venv\Scripts\python.exe
Arguments: scheduler.py --run-once
Start in: C:\path\to\streamsaver
```
Add `STREAMSAVER_PASSPHRASE` as a system environment variable.

---

## Features

| Feature | Description |
|---|---|
| **Services tab** | Add/edit/delete streaming services. Set status: Subscribed, Suspended, Unsubscribed, Not Subscribed. Enter monthly/annual cost and billing date. |
| **Shows tab** | View all Trakt watched shows with season progress, next episode, premiere dates. Manually mark shows as abandoned. |
| **Settings tab** | Set notification email. Configure abandoned detection (manual or auto with 1/3/6/12 month threshold). Sync Trakt and run analysis on demand. |
| **Email reports** | Dracula-themed HTML reports sent to your notification email for suspend and resubscribe recommendations. |

---

## Project structure

```
streamsaver/
├── main.py                        # GUI entry point
├── scheduler.py                   # Daily scheduled runner
├── setup_credentials.py           # Credential setup wizard
├── db.py                          # SQLite init and connection helpers
├── requirements.txt
├── config/                        # credentials.enc + .salt (auto-created)
├── data/                          # streamsaver.db (auto-created)
├── core/
│   ├── trakt_client.py            # Trakt REST API wrapper
│   ├── show_tracker.py            # Sync Trakt data → SQLite
│   ├── service_manager.py         # Service CRUD and cost logic
│   ├── analysis.py                # Suspend / resubscribe engine
│   └── abandoned.py               # Abandoned show detection
├── gui/
│   ├── app.py                     # Main window + credential unlock
│   ├── theme.py                   # Dracula colour palette
│   ├── widgets.py                 # Shared themed widgets + ttk styles
│   ├── services_page.py           # Services tab
│   ├── shows_page.py              # Shows tab
│   └── settings_page.py           # Settings tab
└── email_reports/
    ├── report_builder.py           # HTML report generator
    ├── mailer.py                   # Gmail SMTP sender
    └── templates/base.html         # Dracula email template
```
