"""
email_reports/logo.py
Streamsaver — service logo helper for CID-embedded email images.

Pipeline:
  service dict
  → icon_path (relative, stored in DB)
  → resolve to absolute path
  → load image bytes from disk
  → generate deterministic CID (service_logo_{slug})
  → return img HTML with cid: src + MIMEImage attachment part

Fallback (logo file missing):
  → deterministic color-initial avatar (same container, no CID)

One rendering path. No data URIs. No remote fetches.
"""

import os
import logging
import re
from email.mime.image import MIMEImage

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LOGO_SIZE   = 64
_IMG_SIZE    = 44
_LOGO_BG     = "#1f2230"
_LOGO_BORDER = "#40445c"

_AVATAR_COLORS = ["#bd93f9", "#8be9fd", "#ffb86c", "#ff79c6", "#50fa7b", "#6272a4"]


def _slug(name: str) -> str:
    """Convert service name to a safe CID component: 'Shudder Amazon Channel' → 'shudder_amazon_channel'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _resolve(icon_path: str) -> str:
    """Relative paths resolved against project root; absolute paths passed through."""
    if os.path.isabs(icon_path):
        return icon_path
    return os.path.join(_PROJECT_ROOT, icon_path)


def _wrap_container(inner_html: str, size: int = _LOGO_SIZE) -> str:
    """Standard 64×64 dark bordered container box."""
    return (
        f'<table width="{size}" height="{size}" cellpadding="10" cellspacing="0" '
        f'border="0" bgcolor="{_LOGO_BG}" '
        f'style="background-color:{_LOGO_BG};border-radius:14px;'
        f'border:1px solid {_LOGO_BORDER};width:{size}px;height:{size}px;">'
        f'<tr><td align="center" valign="middle" '
        f'width="{size - 20}" height="{size - 20}">'
        f'{inner_html}</td></tr></table>'
    )


def get_service_logo_cid(
    service: dict,
) -> tuple[str, str | None, MIMEImage | None]:
    """
    Returns (img_html, cid, mime_part).

    If logo file exists on disk:
      - img_html: <img src="cid:service_logo_{slug}" ...> in container
      - cid: deterministic string e.g. "service_logo_shudder_amazon_channel"
      - mime_part: MIMEImage ready to attach to the MIME envelope

    If logo is missing:
      - img_html: letter-avatar HTML in same container
      - cid: None
      - mime_part: None
    """
    name      = service.get("name", "?")
    icon_path = service.get("icon_path")

    if icon_path:
        abs_path = _resolve(icon_path)
        if os.path.exists(abs_path):
            with open(abs_path, "rb") as fh:
                raw = fh.read()
            if raw:
                cid      = f"service_logo_{_slug(name)}"
                ext      = os.path.splitext(abs_path)[1].lstrip(".").lower()
                subtype  = "jpeg" if ext in ("jpg", "jpeg") else ext

                mime_part = MIMEImage(raw, _subtype=subtype)
                mime_part.add_header("Content-ID", f"<{cid}>")
                mime_part.add_header("Content-Disposition", "inline",
                                     filename=f"{_slug(name)}.{ext}")

                img_html = (
                    f'<img src="cid:{cid}" alt="" border="0" '
                    f'width="{_IMG_SIZE}" height="{_IMG_SIZE}" '
                    f'style="display:block;width:{_IMG_SIZE}px;height:{_IMG_SIZE}px;'
                    f'object-fit:contain;border-radius:6px;">'
                )
                logger.info("Attached CID logo: %s (%s)", cid, abs_path)
                return _wrap_container(img_html), cid, mime_part

    logger.warning("Using fallback avatar for %s", name)
    return _wrap_container(_avatar_html(name)), None, None


def _avatar_html(name: str) -> str:
    initial = name[0].upper() if name else "?"
    color   = _AVATAR_COLORS[hash(name) % len(_AVATAR_COLORS)]
    fsize   = _IMG_SIZE // 3
    return (
        f'<table width="{_IMG_SIZE}" height="{_IMG_SIZE}" cellpadding="0" '
        f'cellspacing="0" border="0" bgcolor="{color}" '
        f'style="background-color:{color};border-radius:6px;">'
        f'<tr><td width="{_IMG_SIZE}" height="{_IMG_SIZE}" align="center" '
        f'valign="middle" style="font-size:{fsize}px;font-weight:700;'
        f'color:#1c1f2b;font-family:Arial,Helvetica,sans-serif;">'
        f'{initial}</td></tr></table>'
    )
