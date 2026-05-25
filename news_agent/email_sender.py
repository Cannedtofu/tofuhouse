"""Send the news digest via SMTP email."""

import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import markdown as md

from config import RECIPIENT_EMAIL, SMTP_PASSWORD, SMTP_PORT, SMTP_SERVER, SMTP_USER

logger = logging.getLogger(__name__)

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
         font-size: 15px; line-height: 1.6; color: #1a1a1a; max-width: 700px;
         margin: 0 auto; padding: 24px 16px; }}
  h2   {{ font-size: 20px; margin: 32px 0 8px; border-bottom: 2px solid #e0e0e0;
         padding-bottom: 6px; }}
  h3   {{ font-size: 17px; margin: 24px 0 6px; color: #333; }}
  hr   {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
  p    {{ margin: 8px 0; }}
  ul, ol {{ padding-left: 20px; margin: 8px 0; }}
  li   {{ margin: 4px 0; }}
  a    {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  strong {{ color: #111; }}
  blockquote {{ border-left: 3px solid #ccc; margin: 8px 0; padding: 4px 12px;
               color: #555; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px;
         font-family: monospace; font-size: 13px; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _to_html(markdown_body: str) -> str:
    html_body = md.markdown(
        markdown_body,
        extensions=["extra", "nl2br"],
    )
    return _HTML_WRAPPER.format(body=html_body)


def send_digest(
    markdown_body: str,
    to_email: Optional[str] = None,
    date_label: Optional[str] = None,
) -> bool:
    """
    Send a markdown digest as a multipart email (HTML + plain-text fallback).
    to_email overrides RECIPIENT_EMAIL from config.
    Returns True on success, False on failure.
    """
    recipient = to_email or RECIPIENT_EMAIL
    if not all([SMTP_USER, SMTP_PASSWORD, recipient]):
        logger.error("Email credentials not configured. Set SMTP_USER, SMTP_PASSWORD, RECIPIENT_EMAIL.")
        return False

    label = date_label or date.today().isoformat()
    article_count = markdown_body.count("**[")

    subject = f"📰 News Digest — {label} ({article_count} articles)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = recipient
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))
    msg.attach(MIMEText(_to_html(markdown_body), "html", "utf-8"))

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
