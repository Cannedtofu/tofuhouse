"""
Nitter HTML page fetcher and cursor-based pagination parser.

Used by fetch_nitter_hybrid() in rss.py for daily paginated fetches,
and by scripts/fetch_history.py for historical backfills.

Date format from Nitter's formatters.nim:
  tweet.time.format("MMM d', 'YYYY' · 'h:mm tt' UTC'")
  → e.g. "May 17, 2026 · 10:30 AM UTC"
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from config import NITTER_INSTANCES, NITTER_LOCAL_URL

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\w+ \d+, \d{4}) · (\d+:\d+ [AP]M) UTC")


def _parse_date(title: str) -> Optional[str]:
    """Parse Nitter date title → ISO UTC string, or None."""
    m = _DATE_RE.search(title)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d, %Y %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def _to_xcom_url(href: str) -> str:
    """Convert a Nitter-relative tweet href to an x.com URL."""
    href = href.split("#")[0]  # strip #m anchor
    return "https://x.com" + href if href.startswith("/") else href


def _best_instance() -> str:
    return NITTER_LOCAL_URL if NITTER_LOCAL_URL else NITTER_INSTANCES[0]


def fetch_nitter_html_page(
    handle: str,
    cursor: Optional[str] = None,
    instance: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    """
    Fetch one page of tweets from a Nitter HTML profile page.

    Returns (tweets, next_cursor).
    next_cursor is None when there are no more pages.
    Each tweet dict: {title, url, content, published_at}.
    """
    base = instance or _best_instance()
    url = f"{base}/{handle}"
    if cursor:
        url += f"?cursor={cursor}"

    try:
        resp = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; newsagent/1.0)"},
        )
        if resp.status_code != 200:
            logger.warning("[nitter-html] HTTP %d for %s", resp.status_code, url)
            return [], None
    except Exception as exc:
        logger.warning("[nitter-html] request failed (%s): %s", url, exc)
        return [], None

    soup = BeautifulSoup(resp.text, "html.parser")
    tweets: list[dict] = []

    for item in soup.select(".timeline-item"):
        # Skip "Load newest" items — they carry both classes
        if "show-more" in (item.get("class") or []):
            continue

        date_el = item.select_one(".tweet-date a")
        if not date_el:
            continue

        href = date_el.get("href", "")
        if not href:
            continue

        tweet_url = _to_xcom_url(href)
        published_at = _parse_date(date_el.get("title", ""))

        content_el = item.select_one(".tweet-content")
        content = content_el.get_text(separator=" ", strip=True) if content_el else ""

        tweets.append({
            "title": content[:120] if content else tweet_url,
            "url": tweet_url,
            "content": content,
            "published_at": published_at,
        })

    # "Load more" cursor — sits in a plain div.show-more (not .timeline-item.show-more)
    next_cursor: Optional[str] = None
    for el in soup.select(".show-more a"):
        href = el.get("href", "")
        if "cursor=" in href:
            raw = href.split("cursor=")[1].split("&")[0]
            next_cursor = unquote(raw)
            break

    logger.info(
        "[nitter-html] @%s page: %d tweets, next_cursor=%s",
        handle, len(tweets), "yes" if next_cursor else "no",
    )
    return tweets, next_cursor
