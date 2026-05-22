"""
email_reports/mailer.py
Streamsaver — Gmail SMTP email sender
Author: Noah Lee

Sends HTML email reports via Gmail using an App Password.
Credentials are loaded from the encrypted config file each call,
so the passphrase must have been provided at startup.
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465   # SSL


def send_report(
    to_address: str,
    subject: str,
    html_body: str,
    from_address: Optional[str] = None,
    app_password: Optional[str] = None,
) -> None:
    """
    Send an HTML email via Gmail SMTP.

    If from_address and app_password are not provided, they are loaded
    from the encrypted credentials file using the global passphrase
    set at application startup.

    Args:
        to_address:   Recipient email address.
        subject:      Email subject line.
        html_body:    Full HTML content string.
        from_address: Gmail sender address (optional, loaded from creds if absent).
        app_password: Gmail App Password (optional, loaded from creds if absent).
    """
    if not to_address:
        raise ValueError("Recipient address (to_address) must not be empty.")

    # Resolve credentials if not explicitly passed
    if not from_address or not app_password:
        from_address, app_password = _load_gmail_creds()

    # Build the message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Streamsaver <{from_address}>"
    msg["To"]      = to_address
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=context) as server:
            server.login(from_address, app_password)
            server.sendmail(from_address, to_address, msg.as_string())
        logger.info("Email sent to %s — Subject: %s", to_address, subject)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "Gmail authentication failed. "
            "Verify your sender address and App Password in setup_credentials.py."
        )
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Failed to send email: {exc}") from exc


def _load_gmail_creds() -> tuple[str, str]:
    """
    Load gmail_sender and gmail_app_password from the encrypted credentials file.
    Requires the application to have been unlocked at startup.
    """
    # The app stores the decrypted credentials in memory on the client object.
    # For the standalone mailer (scheduler usage) we require the passphrase via env
    # or prompt. Here we defer to the app-level session; if unavailable we raise.
    try:
        from _runtime import get_credentials  # type: ignore[import]
        creds = get_credentials()
        return creds["gmail_sender"], creds["gmail_app_password"]
    except ImportError:
        raise RuntimeError(
            "Gmail credentials not available. "
            "Ensure the application is unlocked before sending reports."
        )


def send_report_with_credentials(
    creds: dict,
    to_address: str,
    subject: str,
    html_body: str,
) -> None:
    """
    Convenience wrapper that accepts a credentials dict directly.
    Used by the scheduler which loads credentials at startup.
    """
    send_report(
        to_address=to_address,
        subject=subject,
        html_body=html_body,
        from_address=creds["gmail_sender"],
        app_password=creds["gmail_app_password"],
    )
