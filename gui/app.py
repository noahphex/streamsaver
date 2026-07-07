"""
gui/app.py
Streamsaver — Main Tkinter application shell
Author: Noah Lee

Manages the top-level window, tab notebook, credential loading,
and wiring between tabs.
"""

import logging
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from gui import theme

_LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logos", "streamsaver_logo_transparent.png")
from gui.widgets import configure_ttk_styles
from gui.services_page import ServicesPage
from gui.shows_page    import ShowsPage
from gui.settings_page import SettingsPage

logger = logging.getLogger(__name__)

APP_TITLE  = "Streamsaver"
MIN_WIDTH  = 960
MIN_HEIGHT = 620


class StreamsaverApp(tk.Tk):
    """Top-level application window."""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.configure(bg=theme.BG)
        self._client      = None
        self._tmdb_client = None
        self._creds       = {}   # decrypted credentials dict, set after unlock

        configure_ttk_styles()
        self._build_ui()
        self._load_credentials_prompt()

    # ── UI construction ────────────────────────────────────────────────────────

    def _load_logo(self, height: int = 32):
        """Load the sidebar logo, scaled to *height* px. Returns PhotoImage or None."""
        try:
            from PIL import Image, ImageTk
            img = Image.open(_LOGO_PATH).convert("RGBA")
            ratio = height / img.height
            img = img.resize((max(1, int(img.width * ratio)), height), Image.LANCZOS)
            # Composite transparent PNG against the title bar background colour so
            # pixels that Tkinter would otherwise leave transparent render correctly.
            bg_hex = theme.BG_ALT.lstrip("#")
            bg_rgb = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4))
            background = Image.new("RGBA", img.size, bg_rgb + (255,))
            background.paste(img, mask=img.split()[3])
            return ImageTk.PhotoImage(background.convert("RGB"))
        except Exception:
            logger.debug("Could not load logo from %s", _LOGO_PATH, exc_info=True)
            return None

    def _build_ui(self) -> None:
        # Title bar
        title_bar = tk.Frame(self, bg=theme.BG_ALT, height=46)
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        self._logo_img = self._load_logo(height=32)
        if self._logo_img:
            tk.Label(
                title_bar,
                image=self._logo_img,
                bg=theme.BG_ALT,
                bd=0,
            ).pack(side="left", padx=(theme.PAD * 2, 6))
            tk.Label(
                title_bar,
                text="Streamsaver",
                bg=theme.BG_ALT,
                fg=theme.FG,
                font=theme.FONT_TITLE,
            ).pack(side="left", padx=(0, theme.PAD * 2))
        else:
            tk.Label(
                title_bar,
                text="💾  Streamsaver",
                bg=theme.BG_ALT,
                fg=theme.FG,
                font=theme.FONT_TITLE,
            ).pack(side="left", padx=theme.PAD * 2)

        self._status_bar = tk.Label(
            title_bar,
            text="Not connected",
            bg=theme.BG_ALT,
            fg=theme.COMMENT,
            font=theme.FONT_SMALL,
        )
        self._status_bar.pack(side="right", padx=theme.PAD * 2)

        # Notebook tabs
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=0, pady=0)

        self._services_page = ServicesPage(self._notebook)
        self._shows_page    = ShowsPage(self._notebook)
        self._settings_page = SettingsPage(
            self._notebook,
            get_client_fn=lambda: self._client,
            get_tmdb_client_fn=lambda: self._tmdb_client,
            get_creds_fn=lambda: self._creds,
        )

        self._notebook.add(self._services_page, text="  📺  Services  ")
        self._notebook.add(self._shows_page,    text="  🎬  Shows  ")
        self._notebook.add(self._settings_page, text="  ⚙  Settings  ")

        # When sync completes in settings, refresh other tabs
        self._settings_page.bind("<<SyncComplete>>", self._on_sync_complete)

    # ── Credential loading ─────────────────────────────────────────────────────

    def _load_credentials_prompt(self) -> None:
        """Ask for passphrase and attempt to load credentials."""
        from setup_credentials import load_credentials, CRED_FILE
        import os

        if not os.path.exists(CRED_FILE):
            messagebox.showinfo(
                "First Run",
                "No credentials found.\n\n"
                "Please run  setup_credentials.py  from a terminal to set up\n"
                "your Trakt API keys and Gmail App Password, then restart.",
                parent=self,
            )
            self._status_bar.config(
                text="⚠  No credentials — run setup_credentials.py",
                fg=theme.WARNING,
            )
            return

        passphrase = os.environ.get("STREAMSAVER_PASSPHRASE") or simpledialog.askstring(
            "Unlock Streamsaver",
            "Enter your passphrase to decrypt credentials:",
            show="*",
            parent=self,
        )
        if not passphrase:
            self._status_bar.config(text="⚠  Credentials not loaded.", fg=theme.WARNING)
            return

        try:
            creds = load_credentials(passphrase)
            self._init_client(creds)
        except Exception as exc:
            messagebox.showerror(
                "Unlock Failed",
                f"Could not decrypt credentials:\n{exc}\n\nCheck your passphrase.",
                parent=self,
            )
            self._status_bar.config(text="❌  Unlock failed.", fg=theme.DANGER)

    def _init_client(self, creds: dict) -> None:
        """Instantiate TraktClient + TMDbClient, update status bar, seed provider cache."""
        from core.trakt_client import TraktClient
        from core.tmdb_client  import TMDbClient
        from db import db_session

        self._creds = creds   # keep in memory for email reports

        self._client = TraktClient(
            client_id     = creds["trakt_client_id"],
            client_secret = creds["trakt_client_secret"],
            access_token  = creds["trakt_access_token"],
            refresh_token = creds.get("trakt_refresh_token", ""),
            username      = creds.get("trakt_username", ""),
        )

        tmdb_key = creds.get("tmdb_api_key", "")
        if tmdb_key:
            with db_session() as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'provider_country'"
                ).fetchone()
                country = (row["value"] if row and row["value"] else "US")
            self._tmdb_client = TMDbClient(api_key=tmdb_key, country=country)
        else:
            self._tmdb_client = None
            logger.warning("No TMDb API key in credentials — provider list unavailable.")

        username = creds.get("trakt_username", "unknown")
        self._status_bar.config(
            text=f"✅  Connected as @{username}",
            fg=theme.SUCCESS,
        )
        logger.info("Trakt client initialized for user: %s", username)

        # Fetch provider list in background if cache is empty and TMDb is configured
        from core.service_manager import networks_cache_is_empty
        if networks_cache_is_empty() and self._tmdb_client:
            import threading
            self._status_bar.config(
                text=f"✅  Connected as @{username} — fetching streaming providers…",
                fg=theme.SUCCESS,
            )
            threading.Thread(target=self._fetch_networks_bg, daemon=True).start()

    def _fetch_networks_bg(self) -> None:
        """Background thread: fetch TMDb streaming providers and cache them."""
        try:
            from core.service_manager import fetch_and_cache_networks
            count = fetch_and_cache_networks(self._tmdb_client)
            logger.info("Cached %d streaming providers.", count)
            username = self._client.username if self._client else "unknown"
            self.after(0, lambda: self._status_bar.config(
                text=f"✅  Connected as @{username}  ({count} providers loaded)",
                fg=theme.SUCCESS,
            ))
        except Exception as exc:
            logger.warning("Failed to fetch providers: %s", exc)

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_sync_complete(self, event=None) -> None:
        """Refresh shows and services tabs after a successful sync."""
        self._shows_page.refresh()
        self._services_page.refresh()


def run() -> None:
    """Entry point called from main.py."""
    app = StreamsaverApp()
    app.mainloop()
