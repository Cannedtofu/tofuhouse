"""Send the news digest via SMTP email."""

import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from typing import Optional

from config import RECIPIENT_EMAIL, SMTP_PASSWORD, SMTP_PORT, SMTP_SERVER, SMTP_USER

logger = logging.getLogger(__name__)


def send_digest(
    markdown_body: str,
    to_email: Optional[str] = None,
    date_label: Optional[str] = None,
) -> bool:
    """
    Send a markdown digest as a plain-text email.
    to_email overrides RECIPIENT_EMAIL from config.
    Returns True on success, False on failure.
    """
    recipient = to_email or RECIPIENT_EMAIL
    if not all([SMTP_USER, SMTP_PASSWORD, recipient]):
        logger.error("Email credentials not configured. Set SMTP_USER, SMTP_PASSWORD, RECIPIENT_EMAIL.")
        return False

    label = date_label or date.today().isoformat()
    article_count = markdown_body.count("**[")

    msg = EmailMessage()
    msg["Subject"] = f"📰 News Digest — {label} ({article_count} articles)"
    msg["From"] = SMTP_USER
    msg["To"] = recipient
    msg.set_content(markdown_body)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)

        logger.info("Email sent to %s (%d articles)", recipient, article_count)
        return True

    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False

