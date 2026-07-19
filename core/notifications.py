"""
core/notifications.py
Streamsaver notification helpers.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


_COLORS = {
    "error": 0xFF5555,
    "warning": 0xF6C177,
    "success": 0x68D391,
    "info": 0x4FD1C5,
}


def send_discord_notification(
    webhook_url: str,
    title: str,
    message: str,
    *,
    level: str = "error",
    footer: Optional[str] = None,
) -> bool:
    """Send a compact Discord webhook notification."""
    if not webhook_url:
        return False

    embed = {
        "title": title[:256],
        "description": message[:4000],
        "color": _COLORS.get(level, _COLORS["info"]),
    }
    if footer:
        embed["footer"] = {"text": footer[:2048]}

    try:
        resp = requests.post(
            webhook_url,
            json={
                "username": "Streamsaver",
                "embeds": [embed],
                "allowed_mentions": {"parse": []},
            },
            timeout=15,
        )
        if resp.status_code in (200, 204):
            return True
        logger.warning(
            "Discord webhook returned status %s: %s",
            resp.status_code,
            resp.text[:300],
        )
    except requests.RequestException as exc:
        logger.warning("Discord webhook notification failed: %s", exc)
    return False
