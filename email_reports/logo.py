"""
email_reports/logo.py
Single source of truth for service logo HTML in email reports.

Pipeline:
  service dict
  → icon_path (relative, stored in DB)
  → resolve to absolute path at render time
  → load PNG/JPEG from disk
  → base64 encode
  → inline <img src="data:image/...;base64,...">

Fallback (only if file missing):
  → deterministic color-initial avatar (same container dimensions)

No other logo logic should exist anywhere in the rendering stack.
"""

import os
import base64
import logging

logger = logging.getLogger(__name__)

# Project root — one level up from this file's package
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LOGO_SIZE   = 64
_IMG_SIZE    = 44   # max-width / max-height of the image inside the container
_LOGO_BG     = "#1f2230"
_LOGO_BORDER = "#40445c"

_AVATAR_COLORS = ["#bd93f9", "#8be9fd", "#ffb86c", "#ff79c6", "#50fa7b", "#6272a4"]


def _resolve_path(icon_path: str) -> str:
    """
    Convert a stored icon_path to an absolute path.
    Stored paths are relative (e.g. 'assets/logos/8.jpg').
    Absolute paths are passed through unchanged.
    """
    if os.path.isabs(icon_path):
        return icon_path
    return os.path.join(_PROJECT_ROOT, icon_path)


def get_service_logo_html(service: dict) -> str:
    """
    Return a 64×64 email-safe logo block for the given service dict.

    Expects service to have:
      - 'icon_path': relative or absolute path to the logo file (may be None)
      - 'name': display name used for avatar fallback

    Returns pure HTML string. No side effects.
    """
    name      = service.get("name", "?")
    icon_path = service.get("icon_path")
    size      = _LOGO_SIZE
    img_size  = _IMG_SIZE

    # ── Try to load logo from disk ────────────────────────────────────────────
    if icon_path:
        abs_path = _resolve_path(icon_path)
        if os.path.exists(abs_path):
            with open(abs_path, "rb") as fh:
                raw = fh.read()
            if raw:
                encoded = base64.b64encode(raw).decode()
                ext     = os.path.splitext(abs_path)[1].lstrip(".").lower()
                mime    = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                logger.info("Logo loaded: %s", abs_path)

                img_html = (
                    f'<img src="data:{mime};base64,{encoded}" '
                    f'alt="LOGO LOADED" border="0" '
                    f'width="{img_size}" height="{img_size}" '
                    f'style="display:block;width:{img_size}px;height:{img_size}px;'
                    f'object-fit:contain;border-radius:6px;">'
                )
                return _wrap_container(img_html, size, debug=True)
        logger.warning("Missing logo for %s (path: %s)", name, abs_path if icon_path else "none")
    else:
        logger.warning("Missing logo for %s (no icon_path)", name)

    # ── Avatar fallback ───────────────────────────────────────────────────────
    initial = name[0].upper() if name else "?"
    color   = _AVATAR_COLORS[hash(name) % len(_AVATAR_COLORS)]
    fsize   = img_size // 3

    avatar_html = (
        f'<table width="{img_size}" height="{img_size}" cellpadding="0" cellspacing="0" '
        f'border="0" bgcolor="{color}" '
        f'style="background-color:{color};border-radius:6px;">'
        f'<tr><td width="{img_size}" height="{img_size}" align="center" valign="middle" '
        f'style="font-size:{fsize}px;font-weight:700;color:#1c1f2b;'
        f'font-family:Arial,Helvetica,sans-serif;">'
        f'{initial}</td></tr></table>'
    )
    return _wrap_container(avatar_html, size, debug=True)


def _wrap_container(inner_html: str, size: int, debug: bool = False) -> str:
    """Wrap logo content in the standard dark bordered container box."""
    debug_style = "border:2px solid red;" if debug else ""
    return (
        f'<table width="{size}" height="{size}" cellpadding="10" cellspacing="0" '
        f'border="0" bgcolor="{_LOGO_BG}" '
        f'style="background-color:{_LOGO_BG};border-radius:14px;'
        f'border:1px solid {_LOGO_BORDER};width:{size}px;height:{size}px;'
        f'{debug_style}">'
        f'<tr><td align="center" valign="middle" '
        f'width="{size - 20}" height="{size - 20}">'
        f'{inner_html}</td></tr></table>'
    )
