"""Fetcher for 小宇宙 (Xiaoyuzhou) podcast sources.

Scrapes the podcast page HTML for episode links, then fetches each episode
page's __NEXT_DATA__ for metadata (title, shownotes, pubDate, audio URL).
No JS rendering required — episode metadata is server-rendered in __NEXT_DATA__.

Public API:
  fetch_xiaoyuzhou(source_url, known_urls, date_from) -> list[dict]
  get_episode_metadata(episode_id)                   -> dict | None
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from config import DATE_RANGE_DAYS, MAX_ARTICLES_PER_SOURCE

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 15
_EPISODE_ID_RE = re.compile(r"/episode/([a-f0-9]+)")
_NEXT_DATA_RE  = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _extract_next_data(html: str) -> dict | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _strip_html(html_text: str) -> str:
    """Strip HTML tags and return clean plain text."""
    try:
        return BeautifulSoup(html_text, "html.parser").get_text(separator="\n").strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_text).strip()


def get_episode_metadata(episode_id: str) -> dict | None:
    """Fetch metadata for a single episode by ID.

    Returns a dict with keys:
        eid, title, author, description, audio_url, pub_date (ISO str), duration (seconds)
    Returns None if the page cannot be fetched or parsed.
    """
    url = f"https://www.xiaoyuzhoufm.com/episode/{episode_id}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        r.encoding = "utf-8"
        r.raise_for_status()
    except Exception as exc:
        log.warning("Failed to fetch Xiaoyuzhou episode %s: %s", episode_id, exc)
        return None

    data = _extract_next_data(r.text)
    if not data:
        log.warning("No __NEXT_DATA__ for Xiaoyuzhou episode %s", episode_id)
        return None

    ep = data.get("props", {}).get("pageProps", {}).get("episode", {})
    if not ep:
        return None

    # Audio URL lives at ep["media"]["source"]["url"]
    audio_url = None
    try:
        audio_url = ep["media"]["source"]["url"]
    except (KeyError, TypeError):
        pass

    # Author comes from the nested podcast object
    author = None
    try:
        author = ep["podcast"]["author"]
    except (KeyError, TypeError):
        pass

    return {
        "eid":       ep.get("eid", episode_id),
        "title":     ep.get("title", ""),
        "author":    author,
        "description": _strip_html(ep.get("shownotes") or ep.get("description") or ""),
        "audio_url": audio_url,
        "pub_date":  ep.get("pubDate"),   # ISO string e.g. "2026-06-02T11:30:00.000Z"
        "duration":  ep.get("duration"),  # seconds (int)
    }


def fetch_xiaoyuzhou(
    source_url: str,
    known_urls: set | None = None,
    date_from: str | None = None,
) -> list[dict]:
    """Fetch recent episodes from a Xiaoyuzhou podcast page.

    source_url  — https://www.xiaoyuzhoufm.com/podcast/{pid}
    known_urls  — episode URLs already in DB (skipped)
    date_from   — ISO date string; older episodes are skipped

    Returns a list of article dicts:
        title, url, content (shownotes plain text), published_at, needs_full_content=False
    """
    if date_from:
        try:
            cutoff = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        except Exception:
            cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_RANGE_DAYS)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_RANGE_DAYS)

    log.info("Fetching Xiaoyuzhou podcast: %s", source_url)

    try:
        r = requests.get(source_url, headers=_HEADERS, timeout=_TIMEOUT)
        r.encoding = "utf-8"
        r.raise_for_status()
    except Exception as exc:
        log.error("Failed to fetch Xiaoyuzhou podcast page %s: %s", source_url, exc)
        return []

    # Collect unique episode IDs from href="/episode/{id}" links, preserving order
    episode_ids = list(dict.fromkeys(_EPISODE_ID_RE.findall(r.text)))
    log.info("  Found %d episode links on podcast page", len(episode_ids))

    articles = []
    skipped_known = 0
    skipped_old   = 0

    for eid in episode_ids:
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break

        ep_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"

        if known_urls and ep_url in known_urls:
            skipped_known += 1
            continue

        meta = get_episode_metadata(eid)
        if not meta:
            continue

        pub_str = meta["pub_date"]
        if pub_str:
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                if pub_dt < cutoff:
                    skipped_old += 1
                    continue
            except Exception:
                pass

        articles.append({
            "title":             meta["title"],
            "url":               ep_url,
            "content":           meta["description"],
            "published_at":      pub_str,
            "needs_full_content": False,
        })

    if skipped_known:
        log.info("  Skipped %d already-known episodes", skipped_known)
    if skipped_old:
        log.info("  Skipped %d episodes older than cutoff", skipped_old)
    log.info("  → %d episodes from %s", len(articles), source_url)
    return articles
