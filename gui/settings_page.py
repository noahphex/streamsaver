"""
gui/settings_page.py
Streamsaver — Settings tab
Author: Noah Lee

Settings managed here:
  - Notification email address
  - Abandoned detection mode (auto / manual) and threshold
  - Manual Trakt sync trigger
  - Run analysis now (generates email reports)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading

from gui import theme
from gui.widgets import SectionHeader
from db import db_session


def _get_setting(key: str) -> str:
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else ""


def _set_setting(key: str, value: str) -> None:
    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


THRESHOLD_OPTIONS = [
    ("1 month  (30 days)",   "30"),
    ("3 months (90 days)",   "90"),
    ("6 months (180 days)", "180"),
    ("12 months (365 days)", "365"),
]


class SettingsPage(ttk.Frame):
    """Tab: app settings, sync controls, email management."""

    def __init__(self, parent, get_client_fn=None, get_tmdb_client_fn=None, **kwargs):
        """
        Args:
            get_client_fn:      Callable returning a TraktClient (for sync).
            get_tmdb_client_fn: Callable returning a TMDbClient (for provider refresh).
        """
        super().__init__(parent, **kwargs)
        self.configure(style="TFrame")
        self._get_client      = get_client_fn
        self._get_tmdb_client = get_tmdb_client_fn
        self._build_ui()
        self._load()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        wrap = ttk.Frame(self, style="TFrame", padding=theme.PAD * 2)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(1, weight=1)

        row = 0

        # ── Notification email ────────────────────────────────────────────
        SectionHeader(wrap, text="Notification Settings").grid(
            row=row, column=0, columnspan=2, sticky="w",
            pady=(0, theme.PAD_SMALL)
        )
        row += 1

        ttk.Label(wrap, text="Report email address:", style="TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._email_var = tk.StringVar()
        ttk.Entry(wrap, textvariable=self._email_var, width=40).grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1

        # ── Streaming provider country ────────────────────────────────────
        ttk.Label(wrap, text="Provider country (2-letter code):", style="TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._country_var = tk.StringVar(value="US")
        country_entry = ttk.Entry(wrap, textvariable=self._country_var, width=6)
        country_entry.grid(row=row, column=1, sticky="w", pady=4)
        ttk.Label(
            wrap,
            text="Used when refreshing the streaming provider list.",
            style="Dim.TLabel",
            font=theme.FONT_SMALL,
        ).grid(row=row + 1, column=1, sticky="w")
        row += 2

        ttk.Separator(wrap, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=theme.PAD
        )
        row += 1

        # ── Abandoned detection ───────────────────────────────────────────
        SectionHeader(wrap, text="Abandoned Show Detection").grid(
            row=row, column=0, columnspan=2, sticky="w",
            pady=(0, theme.PAD_SMALL)
        )
        row += 1

        ttk.Label(wrap, text="Detection mode:", style="TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._mode_var = tk.StringVar(value="manual")
        mode_frame = ttk.Frame(wrap, style="TFrame")
        mode_frame.grid(row=row, column=1, sticky="w", pady=4)

        ttk.Radiobutton(
            mode_frame, text="Manual only", variable=self._mode_var,
            value="manual", command=self._on_mode_change,
        ).pack(side="left", padx=(0, theme.PAD))
        ttk.Radiobutton(
            mode_frame, text="Automatic", variable=self._mode_var,
            value="auto", command=self._on_mode_change,
        ).pack(side="left")
        row += 1

        ttk.Label(wrap, text="Inactivity threshold:", style="TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD), pady=4
        )
        self._threshold_var = tk.StringVar(value="90")
        self._threshold_cb  = ttk.Combobox(
            wrap,
            textvariable=self._threshold_var,
            values=[label for label, _ in THRESHOLD_OPTIONS],
            state="readonly",
            width=24,
        )
        self._threshold_cb.grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Separator(wrap, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=theme.PAD
        )
        row += 1

        # ── Actions ───────────────────────────────────────────────────────
        SectionHeader(wrap, text="Actions").grid(
            row=row, column=0, columnspan=2, sticky="w",
            pady=(0, theme.PAD_SMALL)
        )
        row += 1

        btn_frame = ttk.Frame(wrap, style="TFrame")
        btn_frame.grid(row=row, column=0, columnspan=2, sticky="w")

        ttk.Button(
            btn_frame, text="💾 Save Settings",
            style="Accent.TButton", command=self._on_save,
        ).pack(side="left", padx=(0, theme.PAD_SMALL))

        ttk.Button(
            btn_frame, text="🔄 Sync Trakt Now", command=self._on_sync,
        ).pack(side="left", padx=(0, theme.PAD_SMALL))

        ttk.Button(
            btn_frame, text="🌐 Refresh Provider List",
            command=self._on_refresh_networks,
        ).pack(side="left", padx=(0, theme.PAD_SMALL))

        ttk.Button(
            btn_frame, text="📧 Run Analysis & Send Report",
            command=self._on_run_analysis,
        ).pack(side="left", padx=(0, theme.PAD_SMALL))
        row += 1

        # Status message
        self._status_lbl = ttk.Label(wrap, text="", style="Dim.TLabel",
                                     font=theme.FONT_SMALL)
        self._status_lbl.grid(row=row, column=0, columnspan=2, sticky="w",
                               pady=(theme.PAD, 0))

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _on_mode_change(self) -> None:
        """Enable/disable threshold combobox based on mode selection."""
        state = "readonly" if self._mode_var.get() == "auto" else "disabled"
        self._threshold_cb.configure(state=state)

    def _load(self) -> None:
        self._email_var.set(_get_setting("notification_email"))
        self._country_var.set(_get_setting("provider_country") or "US")
        mode = _get_setting("abandoned_mode") or "manual"
        self._mode_var.set(mode)
        self._on_mode_change()

        threshold_days = _get_setting("abandoned_threshold_days") or "90"
        for label, value in THRESHOLD_OPTIONS:
            if value == threshold_days:
                self._threshold_var.set(label)
                break

    def _set_status(self, msg: str, color: str = theme.FG_DIM) -> None:
        self._status_lbl.config(text=msg, foreground=color)
        self.update_idletasks()

    # ── Actions ────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        email   = self._email_var.get().strip()
        mode    = self._mode_var.get()
        country = self._country_var.get().strip().upper() or "US"

        # Resolve threshold label → days value
        threshold_days = "90"
        selected_label = self._threshold_var.get()
        for label, value in THRESHOLD_OPTIONS:
            if label == selected_label:
                threshold_days = value
                break

        _set_setting("notification_email",       email)
        _set_setting("abandoned_mode",           mode)
        _set_setting("abandoned_threshold_days", threshold_days)
        _set_setting("provider_country",         country)

        self._set_status("✅  Settings saved.", color=theme.SUCCESS)

    def _on_sync(self) -> None:
        if not self._get_client:
            messagebox.showwarning(
                "Not Ready",
                "No Trakt credentials loaded. Run setup_credentials.py first.",
                parent=self,
            )
            return
        self._set_status("Syncing Trakt data…", color=theme.INFO)

        def _do():
            try:
                from core.show_tracker  import run_full_sync
                from core.trakt_client  import TraktAuthError
                client  = self._get_client()
                results = run_full_sync(client)
                msg = (
                    f"✅  Sync complete — "
                    f"{results['shows']} shows, "
                    f"{results['watchlist']} watchlist, "
                    f"{results['abandoned']} abandoned updates."
                )
                self.after(0, lambda: self._set_status(msg, theme.SUCCESS))
                self.after(0, lambda: self.event_generate("<<SyncComplete>>"))
            except Exception as exc:
                is_auth = "TraktAuthError" in type(exc).__name__ or "Access denied" in str(exc)
                if is_auth:
                    self.after(0, lambda e=exc: messagebox.showerror(
                        "Trakt Auth Error",
                        "Your Trakt access token is expired or invalid.\n\n"
                        "To fix:\n"
                        "1. Visit https://trakt.tv/oauth/applications\n"
                        "2. Get a new Access Token for your app\n"
                        "3. Re-run  setup_credentials.py  and enter the new token\n"
                        "4. Restart Streamsaver",
                        parent=self,
                    ))
                self.after(0, lambda e=exc: self._set_status(
                    f"❌  Sync failed: {e}", theme.DANGER
                ))

        threading.Thread(target=_do, daemon=True).start()

    def _on_refresh_networks(self) -> None:
        """Fetch the latest streaming provider list from TMDb and update the cache."""
        if not self._get_tmdb_client:
            messagebox.showwarning(
                "Not Ready",
                "TMDb client not configured.\n\nRe-run setup_credentials.py to add your TMDb API key.",
                parent=self,
            )
            return
        tmdb_client = self._get_tmdb_client()
        if not tmdb_client:
            messagebox.showwarning(
                "Not Ready",
                "TMDb client not initialized.\nCheck that your TMDb API key is set in credentials.",
                parent=self,
            )
            return

        # Update the client's country from the current setting before fetching
        country = self._country_var.get().strip().upper() or "US"
        tmdb_client.country = country

        self._set_status("Fetching streaming providers from TMDb…", color=theme.INFO)

        def _do():
            try:
                from core.service_manager import fetch_and_cache_networks
                count = fetch_and_cache_networks(tmdb_client)
                self.after(0, lambda: self._set_status(
                    f"✅  Provider list updated — {count} streaming services cached.",
                    theme.SUCCESS,
                ))
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_status(
                    f"❌  Failed to refresh providers: {e}", theme.DANGER
                ))

        threading.Thread(target=_do, daemon=True).start()

    def _on_run_analysis(self) -> None:
        self._set_status("Running analysis…", color=theme.INFO)

        def _do():
            try:
                from core.analysis import run_analysis
                from email_reports.report_builder import build_report
                from email_reports.mailer import send_report

                results = run_analysis()
                notify_email = _get_setting("notification_email")

                sent = 0
                for rec in results["suspend"]:
                    html = build_report("suspend", [rec])
                    send_report(
                        to_address=notify_email,
                        subject=f"Streamsaver: Suspend {rec['name']}",
                        html_body=html,
                    )
                    sent += 1
                for rec in results["resubscribe"]:
                    html = build_report("resubscribe", [rec])
                    send_report(
                        to_address=notify_email,
                        subject=f"Streamsaver: Resubscribe to {rec['name']}",
                        html_body=html,
                    )
                    sent += 1

                msg = (
                    f"✅  Analysis complete — "
                    f"{len(results['suspend'])} suspend, "
                    f"{len(results['resubscribe'])} resubscribe recommendations. "
                    f"{sent} email(s) sent."
                )
                self.after(0, lambda: self._set_status(msg, theme.SUCCESS))
            except Exception as exc:
                self.after(0, lambda e=exc: self._set_status(
                    f"❌  Error: {e}", theme.DANGER
                ))

        threading.Thread(target=_do, daemon=True).start()
