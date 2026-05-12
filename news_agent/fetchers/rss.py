"""Fetcher for RSS/Atom feeds and Nitter (X.com via RSS)."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser
import requests

from config import (
    CONTENT_LENGTH_THRESHOLD,
    DATE_RANGE_DAYS,
    MAX_ARTICLES_PER_SOURCE,
    MIN_ARTICLE_DATE,
    NITTER_INSTANCES,
)

logger = logging.getLogger(__name__)

_MIN_DT = datetime.fromisoformat(MIN_ARTICLE_DATE).replace(tzinfo=timezone.utc)


def _cutoff_dt() -> datetime:
    """Oldest date we'll accept articles from (DATE_RANGE_DAYS ago, floored by MIN_ARTICLE_DATE)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_RANGE_DAYS)
    return max(cutoff, _MIN_DT)


def _parse_published(entry) -> Optional[str]:
    """Return ISO datetime string from a feedparser entry, or None."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "published") and entry.published:
        try:
            dt = parsedate_to_datetime(entry.published)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return None


def _is_too_old(published_at: Optional[str], cutoff: Optional[datetime] = None) -> bool:
    if not published_at:
        return False  # no date → keep it
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < (cutoff or _cutoff_dt())
    except Exception:
        return False


def _parse_cutoff(date_from: Optional[str]) -> Optional[datetime]:
    """Convert a UI date string like '2026-04-10' to a timezone-aware datetime cutoff."""
    if not date_from:
        return None
    try:
        return datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# RSS entry → article dict (basic parse, no HTTP calls)
# ---------------------------------------------------------------------------

def _entry_to_article_basic(entry) -> dict:
    """
    Parse RSS entry fields.

    Deciding whether to visit the original URL via Playwright:
    - If the RSS body contains images (<img tags): always fetch the full page
      so images are captured in the right positions alongside the text.
    - If the RSS body is text-only: use it directly when it meets the length
      threshold; fall back to Playwright only if the text is too short.
    """
    title = getattr(entry, "title", "") or ""
    link = getattr(entry, "link", "") or ""

    body = ""
    if hasattr(entry, "content") and entry.content:
        body = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        body = entry.summary or ""

    has_images = bool(link) and "<img" in body.lower()
    body_plain = re.sub(r"<[^>]+>", " ", body).strip()
    published_at = _parse_published(entry)

    if has_images:
        needs_full = True
    else:
        needs_full = len(body_plain) < CONTENT_LENGTH_THRESHOLD and bool(link)

    return {
        "title": title,
        "url": link,
        "content": body_plain,
        "published_at": published_at,
        "needs_full_content": needs_full,
    }


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------

def fetch_rss(source_url: str, known_urls: set[str] | None = None, date_from: str | None = None) -> list[dict]:
    """Fetch and parse an RSS/Atom feed. Returns list of article dicts.

    date_from: ISO date string; articles published before this date are skipped.
               Falls back to DATE_RANGE_DAYS from config when omitted.
    Articles already in known_urls are skipped (no HTTP call).
    Short-content articles (< CONTENT_LENGTH_THRESHOLD chars) have their full
    content fetched by visiting the article URL via Playwright; falls back to a
    browser-use LLM agent if Playwright yields too little text.
    """
    cutoff = _parse_cutoff(date_from)
    logger.info("Fetching RSS: %s (cutoff: %s)", source_url, (date_from or f"{DATE_RANGE_DAYS}d ago"))
    try:
        feed = feedparser.parse(source_url)
    except Exception as exc:
        logger.error("feedparser error for %s: %s", source_url, exc)
        return []

    logger.info("  Total entries in feed: %d", len(feed.entries))
    articles = []
    skipped_known = 0

    for entry in feed.entries:
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break
        pub = _parse_published(entry)
        if _is_too_old(pub, cutoff):
            continue
        link = getattr(entry, "link", "") or ""
        if not link:
            continue
        if known_urls and link in known_urls:
            skipped_known += 1
            continue
        article = _entry_to_article_basic(entry)
        if not article["url"]:
            continue
        articles.append(article)

    if skipped_known:
        logger.info("  Skipped %d already-known articles", skipped_known)

    # Enrich short-content articles by visiting the full URL via Playwright
    # (falls back to browser-use LLM agent if Playwright alone gets too little content)
    from fetchers.browser_use_fetcher import enrich_with_playwright
    articles = enrich_with_playwright(articles)

    logger.info("  → %d articles from %s", len(articles), source_url)
    return articles


def fetch_nitter(handle: str, known_urls: set[str] | None = None, date_from: str | None = None) -> list[dict]:
    """
    Fetch tweets for a Twitter/X handle via Nitter RSS.
    Tries each Nitter instance in order until one succeeds.
    The source `url` field stores the handle as 'nitter:{handle}'.

    date_from: ISO date string cutoff — tweets older than this are skipped.
    known_urls: set of URLs already in the DB — matching entries are skipped.
    Tweet content comes from the RSS feed itself — no browser enrichment needed.
    """
    cutoff = _parse_cutoff(date_from)
    handle = handle.lstrip("@")
    for instance in NITTER_INSTANCES:
        feed_url = f"{instance}/{handle}/rss"
        logger.info("Trying Nitter instance: %s", feed_url)
        try:
            resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Feedbin/1.0"})
            if resp.status_code == 429:
                logger.warning("  HTTP 429 (rate limited), waiting 5s then retrying once")
                time.sleep(5)
                resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Feedbin/1.0"})
            if resp.status_code != 200:
                logger.warning("  HTTP %d, trying next instance", resp.status_code)
                continue
            if not resp.content:
                logger.warning("  Empty response body, trying next instance")
                continue
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                logger.warning("  Empty feed, trying next instance")
                continue
        except Exception as exc:
            logger.warning("  Instance failed (%s), trying next", exc)
            continue

        articles = []
        skipped_known = 0
        for entry in feed.entries:
            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                break
            pub = _parse_published(entry)
            if _is_too_old(pub, cutoff):
                continue
            link = getattr(entry, "link", "") or ""
            if not link:
                continue
            if known_urls and link in known_urls:
                skipped_known += 1
                continue
            article = _entry_to_article_basic(entry)
            article.pop("needs_full_content", None)  # tweets don't need enrichment
            if not article["url"]:
                continue
            articles.append(article)

        if skipped_known:
            logger.info("  Skipped %d already-known tweets", skipped_known)
        logger.info("  → %d tweets for @%s", len(articles), handle)
        return articles

    logger.error("All Nitter instances failed for @%s", handle)
    return []
