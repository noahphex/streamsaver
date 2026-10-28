"""
core/tmdb_client.py
Streamsaver — TMDb API wrapper
Author: Noah Lee

Fetches streaming provider data (SVOD services available by country)
and show-level provider availability.
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

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        """Make a GET request and return parsed JSON, or None on failure."""
        url = f"{TMDB_API_BASE}{path}"
        base_params = {"api_key": self.api_key, "language": "en-US"}
        if params:
            base_params.update(params)
        try:
            resp = self._session.get(url, params=base_params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("TMDb GET %s failed: %s", path, exc)
            raise

    def get_streaming_providers(self) -> list[dict]:
        """
        Return all TV streaming providers available in the configured country,
        sorted by display_priority (most prominent services first).

        Each entry contains:
            provider_id   (int)  — stable TMDb ID
            provider_name (str)  — human-readable name
            logo_path     (str)  — relative TMDb image path (e.g. /abc123.png)
        """
        data = self._get("/watch/providers/tv", {"watch_region": self.country})
        results = data.get("results", []) if data else []
        return sorted(results, key=lambda r: r.get("display_priority", 9999))

    def search_tv_show(self, title: str, year: int | None = None) -> int | None:
        """
        Search TMDb for a TV show by title and return its TMDb ID, or None if not found.

        Args:
            title: Show title to search for.
            year:  Optional first-air year to narrow results.
        """
        params: dict = {"query": title}
        if year:
            params["first_air_date_year"] = year
        data = self._get("/search/tv", params)
        results = data.get("results", []) if data else []
        if results:
            return results[0].get("id")
        return None

    def get_show_providers(self, tmdb_id: int) -> list[dict]:
        """
        Return the flatrate (subscription) streaming providers for a show
        in the configured country.

        Each entry contains:
            provider_id   (int)  — stable TMDb ID
            provider_name (str)  — human-readable name
            logo_path     (str)  — relative image path
            display_priority (int) — ordering hint
        """
        data = self._get(f"/tv/{tmdb_id}/watch/providers")
        if not data:
            return []
        country_data = data.get("results", {}).get(self.country, {})
        # flatrate = subscription services; ignore rent/buy/ads
        flatrate = country_data.get("flatrate", [])
        return sorted(flatrate, key=lambda p: p.get("display_priority", 9999))
