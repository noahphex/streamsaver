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

            logger.debug(
                "GET %s  params=%s  status=%s",
                url, params, resp.status_code,
            )

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 204:
                return None
            if resp.status_code in (401, 403):
                # Log full response detail to help diagnose auth failures
                logger.error(
                    "Trakt auth error %s — URL: %s\n"
                    "  Request headers: %s\n"
                    "  Response headers: %s\n"
                    "  Response body: %s",
                    resp.status_code, url,
                    dict(self._session.headers),
                    dict(resp.headers),
                    resp.text[:500],
                )
                raise TraktAuthError(
                    f"Access denied ({resp.status_code}) for {url}.\n"
                    "Your Trakt access token may be expired or invalid.\n"
                    "Re-run setup_credentials.py with a fresh access token.\n"
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

    # ── Watch history ──────────────────────────────────────────────────────────

    def get_show_history(
        self,
        start_at: str,
        username: Optional[str] = None,
        limit: int = 200,
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
        params = {"start_at": start_at, "limit": limit, "extended": "full"}
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

    def get_next_episode(self, trakt_id: str) -> Optional[dict]:
        """
        Return metadata for the next episode to air after the current season/episode.
        https://trakt.docs.apiary.io/#reference/episodes/next-episode
        """
        path = f"/shows/{trakt_id}/next_episode"
        params = {"extended": "full"}
        return self._get(path, params=params)
