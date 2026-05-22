"""
core/tmdb_client.py
Streamsaver — TMDb API wrapper
Author: Noah Lee

Fetches streaming provider data (SVOD services available by country).
Only the read-only API key (v3 auth) is required.

Reference: https://developer.themoviedb.org/reference/watch-providers-tv-list
"""

import logging
import requests

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"


class TMDbClient:
    """Thin wrapper around the TMDb v3 REST API (read-only key auth)."""

    def __init__(self, api_key: str, country: str = "US") -> None:
        self.api_key = api_key
        self.country = country.upper()
        self._session = requests.Session()

    def get_streaming_providers(self) -> list[dict]:
        """
        Return all TV streaming providers available in the configured country,
        sorted by display_priority (most prominent services first).

        Each entry contains:
            provider_id   (int)  — stable TMDb ID for this provider
            provider_name (str)  — human-readable name
            logo_path     (str)  — relative TMDb image path (e.g. /abc123.png)
        """
        url    = f"{TMDB_API_BASE}/watch/providers/tv"
        params = {
            "api_key":      self.api_key,
            "watch_region": self.country,
            "language":     "en-US",
        }
        try:
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return sorted(results, key=lambda r: r.get("display_priority", 9999))
        except requests.RequestException as exc:
            logger.error("TMDb provider fetch failed: %s", exc)
            raise
