"""
email_reports/mailer.py
Streamsaver — Gmail SMTP email sender
Author: Noah Lee

Sends HTML email reports via Gmail using an App Password.
Uses multipart/related MIME for CID-embedded logos.
Credentials are loaded from the encrypted config file each call,
so the passphrase must have been provided at startup.
"""

import logging
import smtplib
import ssl
from email.mime.image import MIMEImage
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
    attachments: Optional[dict[str, MIMEImage]] = None,
    from_address: Optional[str] = None,
    app_password: Optional[str] = None,
) -> None:
    """
    Send an HTML email with CID-embedded images via Gmail SMTP.

    Args:
        to_address:   Recipient email address.
        subject:      Email subject line.
        html_body:    Full HTML content string (may contain cid: references).
        attachments:  Dict of {cid_str: MIMEImage} from report_builder.
        from_address: Gmail sender address (optional, loaded from creds if absent).
        app_password: Gmail App Password (optional, loaded from creds if absent).
    """
    if not to_address:
        raise ValueError("Recipient address (to_address) must not be empty.")

    if not from_address or not app_password:
        from_address, app_password = _load_gmail_creds()

    attachments = attachments or {}

    # multipart/related wraps HTML body + inline image attachments.
    # This is required for cid: references to resolve in Gmail.
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"]    = f"Streamsaver <{from_address}>"
    msg["To"]      = to_address
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attach each unique logo once; deduplicated by CID key from build_digest.
    attached_cids: set[str] = set()
    for cid, mime_part in attachments.items():
        if cid not in attached_cids:
            msg.attach(mime_part)
            attached_cids.add(cid)
            logger.info("Attached CID: %s", cid)

    # Pre-send validation: warn if any cid: ref in HTML has no attachment
    missing = [f"cid:{c}" for c in _extract_cid_refs(html_body) if c not in attached_cids]
    if missing:
        logger.warning("CID refs in HTML without matching attachment: %s", missing)

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


def _extract_cid_refs(html: str) -> list[str]:
    """Return all CID values referenced in src="cid:..." within the HTML."""
    import re
    return re.findall(r'src="cid:([^"]+)"', html)


def _load_gmail_creds() -> tuple[str, str]:
    """
    Load gmail_sender and gmail_app_password from the encrypted credentials file.
    Requires the application to have been unlocked at startup.
    """
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
    attachments: Optional[dict[str, MIMEImage]] = None,
) -> None:
    """
    Convenience wrapper that accepts a credentials dict directly.
    Used by the scheduler which loads credentials at startup.
    """
    send_report(
        to_address=to_address,
        subject=subject,
        html_body=html_body,
        attachments=attachments,
        from_address=creds["gmail_sender"],
        app_password=creds["gmail_app_password"],
    )
