"""
setup_credentials.py
Streamsaver — Secure Credential Setup Wizard
Author: Noah Lee

Run this once to securely store your Trakt API credentials and Gmail
App Password. Credentials are encrypted using Fernet symmetric encryption
with a key derived from your passphrase via PBKDF2-HMAC-SHA256.

Usage:
    python setup_credentials.py
"""

import os
import base64
import getpass
import json
import webbrowser

import requests

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR  = os.path.join(BASE_DIR, "config")
CRED_FILE   = os.path.join(CONFIG_DIR, "credentials.enc")
SALT_FILE   = os.path.join(CONFIG_DIR, ".salt")

TRAKT_OAUTH_AUTHORIZE = "https://trakt.tv/oauth/authorize"
TRAKT_OAUTH_TOKEN     = "https://api.trakt.tv/oauth/token"
TRAKT_REDIRECT_URI    = "urn:ietf:wg:oauth:2.0:oob"  # PIN / out-of-band flow

# ── Encryption helpers ─────────────────────────────────────────────────────────

def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key from a passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def _load_or_create_salt() -> bytes:
    """Load existing salt or generate and persist a new one."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(SALT_FILE):
        with open(SALT_FILE, "rb") as f:
            return f.read()
    salt = os.urandom(16)
    with open(SALT_FILE, "wb") as f:
        f.write(salt)
    return salt


def encrypt_credentials(data: dict, passphrase: str, quiet: bool = False) -> None:
    """Encrypt and save credentials to disk."""
    salt   = _load_or_create_salt()
    key    = _derive_key(passphrase, salt)
    fernet = Fernet(key)
    token  = fernet.encrypt(json.dumps(data).encode())
    with open(CRED_FILE, "wb") as f:
        f.write(token)
    if not quiet:
        print(f"\n✅  Credentials saved to: {CRED_FILE}")


def load_credentials(passphrase: str) -> dict:
    """Decrypt and return credentials from disk."""
    if not os.path.exists(CRED_FILE):
        raise FileNotFoundError(
            "No credentials file found. Run setup_credentials.py first."
        )
    salt   = _load_or_create_salt()
    key    = _derive_key(passphrase, salt)
    fernet = Fernet(key)
    with open(CRED_FILE, "rb") as f:
        token = f.read()
    return json.loads(fernet.decrypt(token).decode())


# ── Wizard ─────────────────────────────────────────────────────────────────────

def _prompt(label: str, secret: bool = False, default: str = "") -> str:
    """Prompt the user for input, optionally masking it."""
    if default:
        display = f"{label} [{default}]: "
    else:
        display = f"{label}: "

    if secret:
        value = getpass.getpass(display)
    else:
        value = input(display)

    return value.strip() or default


def _trakt_oauth_flow(client_id: str, client_secret: str) -> tuple[str, str]:
    """
    Run the Trakt PIN-based OAuth flow.

    Opens the Trakt authorization page in the default browser, asks the user
    to enter the displayed PIN, then exchanges it for access + refresh tokens.

    Returns:
        (access_token, refresh_token)
    """
    auth_url = (
        f"{TRAKT_OAUTH_AUTHORIZE}"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={TRAKT_REDIRECT_URI}"
    )

    print("\n  Opening Trakt authorization page in your browser…")
    print(f"  If it doesn't open, visit:\n    {auth_url}\n")
    webbrowser.open(auth_url)

    print("  After authorizing, Trakt will display a PIN code.")
    while True:
        pin = input("  Enter the PIN from Trakt: ").strip()
        if pin:
            break
        print("  ⚠  PIN cannot be empty.")

    print("  Exchanging PIN for access token…")
    try:
        resp = requests.post(
            TRAKT_OAUTH_TOKEN,
            json={
                "code":          pin,
                "client_id":     client_id,
                "client_secret": client_secret,
                "redirect_uri":  TRAKT_REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
            headers={
                "User-Agent":    "Streamsaver/1.0",
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"\n  ❌  Token exchange failed: {exc}")
        print("  Please check your Client ID and Client Secret and try again.")
        raise SystemExit(1)

    access_token  = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")

    if not access_token:
        print(f"\n  ❌  No access token in response: {data}")
        raise SystemExit(1)

    print("  ✅  Authorization successful!\n")
    return access_token, refresh_token


def run_wizard() -> None:
    print("=" * 60)
    print("  Streamsaver — Credential Setup Wizard")
    print("=" * 60)
    print(
        "\nThis wizard will encrypt and store your Trakt API credentials\n"
        "and Gmail App Password.\n"
        "You will need this passphrase every time Streamsaver starts.\n"
    )

    # ── Passphrase ──────────────────────────────────────────────────────────
    while True:
        passphrase  = getpass.getpass("Create a passphrase (min 8 chars): ").strip()
        passphrase2 = getpass.getpass("Confirm passphrase: ").strip()
        if len(passphrase) < 8:
            print("  ⚠  Passphrase must be at least 8 characters.\n")
        elif passphrase != passphrase2:
            print("  ⚠  Passphrases do not match. Try again.\n")
        else:
            break

    # ── Trakt credentials ───────────────────────────────────────────────────
    print("\n── Trakt API ──────────────────────────────────────────────────")
    print("  Get your Client ID and Secret at:")
    print("  https://trakt.tv/oauth/applications\n")
    trakt_client_id     = _prompt("Client ID")
    trakt_client_secret = _prompt("Client Secret", secret=True)
    trakt_username      = _prompt("Trakt Username")

    print()
    trakt_access_token, trakt_refresh_token = _trakt_oauth_flow(
        trakt_client_id, trakt_client_secret
    )

    # ── TMDb API ─────────────────────────────────────────────────────────────
    print("\n── TMDb API ───────────────────────────────────────────────────")
    print("  Used to fetch streaming provider lists (SVOD services by country).")
    print("  Get a free read-only API key at: https://www.themoviedb.org/settings/api\n")
    tmdb_api_key = _prompt("TMDb Read-Only API Key (leave blank to skip)")

    # ── Gmail SMTP ──────────────────────────────────────────────────────────
    print("\n── Gmail SMTP ─────────────────────────────────────────────────")
    print("  Use a Gmail App Password (not your main password).")
    print("  Generate one at: https://myaccount.google.com/apppasswords\n")
    gmail_sender      = _prompt("Gmail sender address")
    gmail_app_password = _prompt("Gmail App Password", secret=True)

    # ── Notification email ──────────────────────────────────────────────────
    print("\n── Notification Settings ──────────────────────────────────────")
    notify_email = _prompt(
        "Notification email address (reports will be sent here)",
        default=gmail_sender,
    )

    credentials = {
        "trakt_client_id":     trakt_client_id,
        "trakt_client_secret": trakt_client_secret,
        "trakt_access_token":  trakt_access_token,
        "trakt_refresh_token": trakt_refresh_token,
        "trakt_username":      trakt_username,
        "tmdb_api_key":        tmdb_api_key,
        "gmail_sender":        gmail_sender,
        "gmail_app_password":  gmail_app_password,
        "notify_email":        notify_email,
    }

    encrypt_credentials(credentials, passphrase)
    print(
        "\nTo update your credentials in the future, re-run this script.\n"
        "Keep your passphrase safe — it cannot be recovered.\n"
    )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_wizard()
