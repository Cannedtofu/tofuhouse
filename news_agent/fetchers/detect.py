"""
Auto-detect the correct source type and canonical feed URL for a user-supplied URL.

Detection order:
1. X.com / Twitter URL  →  nitter
2. URL is itself a valid RSS/Atom feed  →  rss  (use URL as-is)
3. Page HTML contains <link rel="alternate"> RSS autodiscovery tag  →  rss
4. Common feed path probing (/feed, /rss, /feed.xml, …)  →  rss
5. Fallback  →  web
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
_TIMEOUT = 10
_FEED_PATHS = ["/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/feeds/posts/default"]

_X_PATTERN           = re.compile(r"https?://(www\.)?(x|twitter)\.com/", re.I)
_YOUTUBE_PATTERN     = re.compile(r"https?://(?:www\.)?youtube\.com/", re.I)
_XIAOYUZHOU_PATTERN  = re.compile(r"https?://(?:www\.)?xiaoyuzhoufm\.com/podcast/", re.I)
_BILIBILI_SPACE_RE   = re.compile(r"https?://space\.bilibili\.com/\d+", re.I)


def _is_valid_feed(url: str) -> bool:
    """Return True if feedparser can parse the URL and finds at least one entry or channel title."""
    try:
        feed = feedparser.parse(url)
        return bool(feed.get("feed", {}).get("title") or feed.entries)
    except Exception:
        return False


def _fetch_html(url: str) -> str:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.ok:
            return resp.text
    except Exception:
        pass
    return ""


def _find_rss_in_html(html: str, base_url: str) -> str | None:
    """Look for <link rel='alternate' type='application/rss+xml'> autodiscovery tags."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("link", rel=lambda r: r and "alternate" in r):
        mime = tag.get("type", "")
        if "rss" in mime or "atom" in mime:
            href = tag.get("href", "")
            if href:
                return urljoin(base_url, href)
    return None


def _youtube_feed_url(url: str) -> str | None:
    """Return the RSS feed URL for a YouTube channel page, or None on failure."""
    if "feeds/videos.xml" in url:
        return url
    html = _fetch_html(url)
    if html:
        feed_url = _find_rss_in_html(html, url)
        if feed_url and "youtube.com/feeds" in feed_url:
            return feed_url
    return None


def _probe_feed_paths(base_url: str) -> str | None:
    """Try common feed paths on the same domain and return the first that works."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for path in _FEED_PATHS:
        candidate = root + path
        if _is_valid_feed(candidate):
            logger.info("[detect] found feed at %s", candidate)
            return candidate
    return None


def detect_source(raw_url: str) -> dict:
    """
    Detect the source type and canonical URL for a user-supplied input.

    Returns a dict:
        {
            "type":    "rss" | "nitter" | "web",
            "url":     <canonical URL to store — feed URL for rss, handle for nitter>,
            "display": <human-readable description of what was found>,
            "ok":      True | False,
            "error":   <error message if ok=False>,
        }
    """
    url = raw_url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    # 0. Bilibili user space
    if _BILIBILI_SPACE_RE.match(url):
        from fetchers.bilibili import extract_uid
        uid = extract_uid(url)
        canonical = f"https://space.bilibili.com/{uid}" if uid else url
        return {
            "type":    "bilibili",
            "url":     canonical,
            "display": f"Bilibili 用户空间 UID {uid}" if uid else "Bilibili 用户空间",
            "ok":      bool(uid),
            "error":   None if uid else "无法从链接中识别 UID",
        }

    # 0a. Xiaoyuzhou podcast page
    if _XIAOYUZHOU_PATTERN.match(url):
        return {
            "type":    "xiaoyuzhou",
            "url":     url,
            "display": f"小宇宙播客: {url}",
            "ok":      True,
            "error":   None,
        }

    # 1. YouTube channel URL → youtube type
    if _YOUTUBE_PATTERN.match(url):
        feed_url = _youtube_feed_url(url)
        if feed_url and _is_valid_feed(feed_url):
            return {
                "type": "youtube",
                "url": feed_url,
                "display": f"YouTube 频道 RSS: {feed_url}",
                "ok": True,
                "error": None,
            }
        return {
            "type": "youtube",
            "url": url,
            "display": "",
            "ok": False,
            "error": "找不到该 YouTube 频道的 RSS 地址，请确认链接是频道主页（如 youtube.com/@handle 或 /channel/UCxxx）。",
        }

    # 1. X.com / Twitter → Nitter
    if _X_PATTERN.match(url):
        handle = _X_PATTERN.sub("", url).rstrip("/").split("/")[0].lstrip("@")
        return {
            "type": "nitter",
            "url": f"nitter:{handle}",
            "display": f"X.com account @{handle} (via Nitter RSS)",
            "ok": True,
            "error": None,
        }

    # 2. URL is itself a valid feed
    if _is_valid_feed(url):
        return {
            "type": "rss",
            "url": url,
            "display": f"RSS/Atom feed: {url}",
            "ok": True,
            "error": None,
        }

    # 3. Fetch the page and look for autodiscovery tags
    html = _fetch_html(url)
    if html:
        feed_url = _find_rss_in_html(html, url)
        if feed_url and _is_valid_feed(feed_url):
            return {
                "type": "rss",
                "url": feed_url,
                "display": f"RSS feed discovered: {feed_url}",
                "ok": True,
                "error": None,
            }

        # 4. Probe common feed paths
        feed_url = _probe_feed_paths(url)
        if feed_url:
            return {
                "type": "rss",
                "url": feed_url,
                "display": f"RSS feed found at: {feed_url}",
                "ok": True,
                "error": None,
            }

        # 5. Fallback — treat as web scrape source
        return {
            "type": "web",
            "url": url,
            "display": f"No RSS feed found — will scrape: {url}",
            "ok": True,
            "error": None,
        }

    return {
        "type": "web",
        "url": url,
        "display": f"Could not fetch page — will try scraping: {url}",
        "ok": True,
        "error": None,
    }
