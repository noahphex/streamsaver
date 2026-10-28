"""
core/trakt_client.py
Streamsaver — Trakt API wrapper
Author: Noah Lee

All requests include the required Trakt headers:
    trakt-api-key, trakt-api-version: 2, Accept: application/json
    Authorization: Bearer <access_token>

Reference: https://trakt.docs.apiary.io/#introduction/required-headers
"""

import logging
import time
from typing import Any, Callable, Optional

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
        token_refresh_callback: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self.username      = username
        self._token_refresh_callback = token_refresh_callback
        self._session      = requests.Session()
        self._session.headers.update(self._build_headers())
        logger.debug(
            "TraktClient init — username=%s  client_id=%s…  token=%s…",
            username,
            client_id[:8] if client_id else "(none)",
            access_token[:8] if access_token else "(none)",
        )

    # ── Headers ────────────────────────────────────────────────────────────────

    def _build_headers(self) -> dict:
        return {
            "User-Agent":        "Streamsaver/1.0",
            "Content-Type":      "application/json",
            "trakt-api-version": "2",
            "trakt-api-key":     self.client_id,
            "Accept":            "application/json",
            "Authorization":     f"Bearer {self.access_token}",
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
        data, _headers = self._get_with_headers(path, params=params, retry=retry)
        return data

    def _get_with_headers(
        self,
        path: str,
        params: Optional[dict] = None,
        retry: int = 3,
    ) -> tuple[Any, dict]:
        url = f"{TRAKT_API_BASE}{path}"
        refreshed = False
        for attempt in range(retry):
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                logger.warning("Network error on %s: %s", url, exc)
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

            logger.debug(
                "GET %s  params=%s  status=%s",
                url, params, resp.status_code,
            )

            if resp.status_code == 200:
                return resp.json(), dict(resp.headers)
            if resp.status_code == 204:
                return None, dict(resp.headers)
            if resp.status_code in (401, 403):
                if resp.status_code == 401 and not refreshed and self._refresh_access_token():
                    refreshed = True
                    continue
                # Never log request headers: they carry the bearer token and API key.
                logger.error(
                    "Trakt auth error %s — URL: %s\n"
                    "  Response headers: %s\n"
                    "  Response body: %s",
                    resp.status_code, url,
                    dict(resp.headers),
                    resp.text[:500],
                )
                raise TraktAuthError(
                    f"Access denied ({resp.status_code}) for {url}.\n"
                    "Your Trakt access token may be expired or invalid.\n"
                    "Re-authorize Trakt from Settings -> Update Credentials.\n"
                    f"Full details written to data/streamsaver.log"
                )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning("Rate limited. Retrying after %ss.", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code == 404:
                logger.debug("404 Not found: %s", url)
                return None

            logger.error(
                "Unexpected status %s from %s — body: %s",
                resp.status_code, url, resp.text[:300],
            )
            resp.raise_for_status()

        raise TraktRateLimitError(f"Request to {url} failed after {retry} attempts.")

    def _refresh_access_token(self) -> bool:
        """Refresh the access token when Trakt returns 401."""
        if not (self.client_id and self.client_secret and self.refresh_token):
            return False
        try:
            resp = requests.post(
                f"{TRAKT_API_BASE}/oauth/token",
                json={
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            access_token = payload.get("access_token")
            if not access_token:
                logger.warning("Trakt token refresh response did not include an access token.")
                return False
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Trakt token refresh failed: %s", exc)
            return False

        self.access_token = access_token
        self.refresh_token = payload.get("refresh_token", self.refresh_token)
        self._refresh_headers()
        logger.info("Refreshed Trakt access token.")
        if self._token_refresh_callback:
            # The refresh itself worked, so a save failure must not fail this request.
            try:
                self._token_refresh_callback(
                    {
                        "access_token": self.access_token,
                        "refresh_token": self.refresh_token,
                        "expires_in": payload.get("expires_in"),
                        "created_at": payload.get("created_at"),
                        "scope": payload.get("scope"),
                        "token_type": payload.get("token_type"),
                    }
                )
            except Exception as exc:
                logger.error("Refreshed Trakt tokens could not be saved: %s", exc)
        return True

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

    def get_show_history(
        self,
        start_at: str,
        username: Optional[str] = None,
        limit: int = 200,
        page: int = 1,
    ) -> list[dict]:
        """
        Return episode-level watch history for TV shows since start_at.

        Used for incremental syncs — only fetches activity since the last sync
        timestamp rather than pulling all-time history.

        Args:
            start_at: ISO 8601 datetime string, e.g. "2025-05-22T00:00:00.000Z"
            limit:    Max episodes to return per page (up to 1000 per Trakt docs).

        https://trakt.docs.apiary.io/#reference/users/history/get-watched-history
        """
        user   = username or self.username
        path   = f"/users/{user}/history/shows"
        params = {"start_at": start_at, "limit": limit, "page": page, "extended": "full"}
        result = self._get(path, params=params)
        return result or []

    def get_all_show_history(
        self,
        start_at: str,
        username: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Paginate through show history since start_at."""
        user = username or self.username
        path = f"/users/{user}/history/shows"
        page = 1
        rows: list[dict] = []
        while True:
            params = {"start_at": start_at, "limit": limit, "page": page, "extended": "full"}
            batch, headers = self._get_with_headers(path, params=params)
            batch = batch or []
            rows.extend(batch)
            page_count = int(headers.get("X-Pagination-Page-Count", "1") or "1")
            if page >= page_count or not batch:
                return rows
            page += 1


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


# ── Device-code login ──────────────────────────────────────────────────────────
# Trakt's browser PIN page (urn:ietf:wg:oauth:2.0:oob) hangs on their new sign-in site;
# device codes only need the user to type a short code at trakt.tv/activate.
# https://trakt.docs.apiary.io/#reference/authentication-devices

_DEVICE_HEADERS = {"User-Agent": "Streamsaver/2.5", "Content-Type": "application/json"}
_DEVICE_ERRORS = {
    404: "Invalid device code.",
    409: "This code was already used.",
    410: "The code expired. Start again.",
    418: "Authorization was denied on Trakt.",
}


def request_device_code(client_id: str) -> dict:
    """Start device login. Returns device_code, user_code, verification_url, expires_in, interval."""
    resp = requests.post(f"{TRAKT_API_BASE}/oauth/device/code", json={"client_id": client_id},
                         headers=_DEVICE_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def poll_device_token(client_id: str, client_secret: str, device_code: str) -> Optional[dict]:
    """
    Check once whether the user approved the device code.
    Returns the token payload when approved, None while still pending.
    Raises RuntimeError when the code can no longer succeed.
    """
    resp = requests.post(
        f"{TRAKT_API_BASE}/oauth/device/token",
        json={"code": device_code, "client_id": client_id, "client_secret": client_secret},
        headers=_DEVICE_HEADERS,
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json()
        if not data.get("access_token"):
            raise RuntimeError("Trakt did not return an access token.")
        return data
    if resp.status_code in (400, 429):  # pending / polling too fast
        return None
    raise RuntimeError(_DEVICE_ERRORS.get(resp.status_code, f"Trakt returned {resp.status_code}."))
