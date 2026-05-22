"""
core/trakt_client.py
Streamsaver — Trakt API wrapper
Author: Noah Lee

All requests include the required Trakt headers:
    trakt-api-key, trakt-api-version: 2, Accept: application/json
    Authorization: Bearer <access_token>

Reference: https://trakt.docs.apiary.io/#introduction/required-headers
"""

import time
import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

TRAKT_API_BASE = "https://api.trakt.tv"


class TraktAuthError(Exception):
    """Raised when the API returns 401 Unauthorized."""


class TraktRateLimitError(Exception):
    """Raised when the API returns 429 Too Many Requests."""


class TraktClient:
    """Thin wrapper around the Trakt REST API."""

    def __init__(
        self,
        client_id: str,
        access_token: str,
        client_secret: str = "",
        refresh_token: str = "",
        username: str = "",
    ) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.username      = username
        self._session      = requests.Session()
        self._session.headers.update(self._build_headers())

    # ── Headers ────────────────────────────────────────────────────────────────

    def _build_headers(self) -> dict:
        return {
            "Content-Type":    "application/json",
            "trakt-api-version": "2",
            "trakt-api-key":   self.client_id,
            "Accept":          "application/json",
            "Authorization":   f"Bearer {self.access_token}",
        }

    def _refresh_headers(self) -> None:
        self._session.headers.update(self._build_headers())

    # ── Low-level request ──────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        retry: int = 3,
    ) -> Any:
        url = f"{TRAKT_API_BASE}{path}"
        for attempt in range(retry):
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                logger.warning("Network error on %s: %s", url, exc)
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 204:
                return None
            if resp.status_code in (401, 403):
                raise TraktAuthError(
                    f"Access denied ({resp.status_code}) for {url}.\n"
                    "Your Trakt access token may be expired or invalid.\n"
                    "Re-run setup_credentials.py with a fresh access token."
                )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning("Rate limited. Retrying after %ss.", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code == 404:
                logger.debug("404 Not found: %s", url)
                return None

            resp.raise_for_status()

        raise TraktRateLimitError(f"Request to {url} failed after {retry} attempts.")

    # ── Watch history ──────────────────────────────────────────────────────────

    def get_watch_history(
        self,
        username: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return a user's TV episode watch history, newest first.
        https://trakt.docs.apiary.io/#reference/users/history/get-watched-history
        """
        user = username or self.username
        path = f"/users/{user}/history/episodes"
        params = {"page": page, "limit": limit, "extended": "full"}
        result = self._get(path, params=params)
        return result or []

    def get_all_watch_history(self, username: Optional[str] = None) -> list[dict]:
        """Paginate through all TV episode watch history and return full list."""
        user    = username or self.username
        all_items: list[dict] = []
        page = 1
        while True:
            batch = self.get_watch_history(username=user, page=page, limit=100)
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return all_items

    # ── Watched shows summary ──────────────────────────────────────────────────

    def get_watched_shows(self, username: Optional[str] = None) -> list[dict]:
        """
        Return list of shows a user has watched with play/episode/season counts.
        https://trakt.docs.apiary.io/#reference/users/watched/get-watched
        """
        user   = username or self.username
        path   = f"/users/{user}/watched/shows"
        params = {"extended": "noseasons"}
        result = self._get(path, params=params)
        return result or []

    # ── Watchlist ──────────────────────────────────────────────────────────────

    def get_watchlist(self, username: Optional[str] = None) -> list[dict]:
        """
        Return a user's TV show watchlist (TV shows only, movies excluded).
        https://trakt.docs.apiary.io/#reference/users/watchlist/get-watchlist
        """
        user   = username or self.username
        path   = f"/users/{user}/watchlist/shows"
        params = {"extended": "full"}
        result = self._get(path, params=params)
        return result or []

    # ── Show details ───────────────────────────────────────────────────────────

    def get_show_summary(self, trakt_id: str) -> Optional[dict]:
        """
        Return full show metadata: title, status, network, aired episodes, etc.
        https://trakt.docs.apiary.io/#reference/shows/summary/get-a-single-show
        """
        path   = f"/shows/{trakt_id}"
        params = {"extended": "full"}
        return self._get(path, params=params)

    def get_show_seasons(self, trakt_id: str) -> list[dict]:
        """
        Return season list with episode counts.
        https://trakt.docs.apiary.io/#reference/seasons/summary/get-all-seasons-for-a-show
        """
        path   = f"/shows/{trakt_id}/seasons"
        params = {"extended": "full"}
        result = self._get(path, params=params)
        return result or []

    def get_season_episodes(self, trakt_id: str, season: int) -> list[dict]:
        """
        Return all episodes in a specific season with full metadata.
        https://trakt.docs.apiary.io/#reference/seasons/season/get-single-season-for-a-show
        """
        path   = f"/shows/{trakt_id}/seasons/{season}"
        params = {"extended": "full"}
        result = self._get(path, params=params)
        return result or []

    # ── Episode progress ───────────────────────────────────────────────────────

    def get_show_progress(
        self, trakt_id: str, username: Optional[str] = None
    ) -> Optional[dict]:
        """
        Return watched progress for a show (last watched episode, next episode,
        completed seasons, etc.)
        https://trakt.docs.apiary.io/#reference/shows/watched-progress/get-show-watched-progress
        """
        user   = username or self.username
        path   = f"/shows/{trakt_id}/progress/watched"
        params = {"hidden": False, "specials": False, "count_specials": False}
        return self._get(path, params=params)

    def get_next_episode(self, trakt_id: str) -> Optional[dict]:
        """
        Return metadata for the next episode to air after the current season/episode.
        https://trakt.docs.apiary.io/#reference/episodes/next-episode
        """
        path = f"/shows/{trakt_id}/next_episode"
        params = {"extended": "full"}
        return self._get(path, params=params)

    def get_last_episode(self, trakt_id: str) -> Optional[dict]:
        """
        Return metadata for the most recently aired episode.
        https://trakt.docs.apiary.io/#reference/episodes/last-episode
        """
        path = f"/shows/{trakt_id}/last_episode"
        params = {"extended": "full"}
        return self._get(path, params=params)

    # ── Networks / Services ────────────────────────────────────────────────────

    def get_networks(self) -> list[dict]:
        """
        Return all known networks from Trakt (used to populate service list).
        This is a public endpoint — only the API key header is needed.
        https://trakt.docs.apiary.io/#reference/networks
        """
        result = self._get_public("/networks")
        return result or []

    def _get_public(self, path: str, params: Optional[dict] = None) -> Any:
        """
        GET request using only the API key header (no Bearer token).
        Used for Trakt public endpoints that don't require OAuth.
        """
        url     = f"{TRAKT_API_BASE}{path}"
        headers = {
            "Content-Type":      "application/json",
            "trakt-api-version": "2",
            "trakt-api-key":     self.client_id,
            "Accept":            "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json() if resp.status_code == 200 else None
        except requests.RequestException as exc:
            logger.warning("Public GET failed for %s: %s", url, exc)
            raise

    # ── Search ─────────────────────────────────────────────────────────────────

    def search_shows(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search Trakt for TV shows matching a query string.
        https://trakt.docs.apiary.io/#reference/search/text-query/get-text-query-results
        """
        path   = "/search/show"
        params = {"query": query, "limit": limit, "extended": "full"}
        result = self._get(path, params=params)
        return result or []
