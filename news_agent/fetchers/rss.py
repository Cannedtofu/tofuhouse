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
import trafilatura

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
    """Parse RSS entry fields only. Sets needs_full_content=True if body is short."""
    title = getattr(entry, "title", "") or ""
    link = getattr(entry, "link", "") or ""

    body = ""
    if hasattr(entry, "content") and entry.content:
        body = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        body = entry.summary or ""

    body_plain = re.sub(r"<[^>]+>", " ", body).strip()
    published_at = _parse_published(entry)

    return {
        "title": title,
        "url": link,
        "content": body_plain,
        "published_at": published_at,
        "needs_full_content": len(body_plain) < CONTENT_LENGTH_THRESHOLD and bool(link),
    }


# ---------------------------------------------------------------------------
# Selenium-based full-content enrichment
# ---------------------------------------------------------------------------

def _make_uc_driver():
    """Create an undetected-chromedriver headless instance."""
    import undetected_chromedriver as uc
    options = uc.ChromeOptions()
    # Headless mode is detected by Cloudflare's fingerprinting (empty plugin list,
    # SwiftShader WebGL renderer, mismatched screen dimensions, etc.).
    # Running headed but off-screen passes all fingerprint checks while staying
    # out of the way on Windows (no virtual display available).
    options.add_argument("--window-position=-32000,-32000")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    driver = uc.Chrome(options=options, version_main=146)
    return driver


_CF_MARKERS = ("just a moment", "cf-browser-verification", "cf_chl_opt", "ray id")


def _wait_past_cloudflare(driver, url: str, poll_interval: float = 2.0, timeout: float = 20.0) -> str:
    """
    Block until the Cloudflare JS challenge clears or timeout expires.
    Returns the final page_source.
    Cloudflare's JS challenge typically resolves within 5-10 s in a real Chrome session.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        src = driver.page_source
        lower = src.lower()
        if not any(m in lower for m in _CF_MARKERS):
            page_len = len(src)
            logger.info("    [selenium] Cloudflare cleared — page_source: %d chars", page_len)
            return src
        remaining = deadline - time.time()
        logger.info("    [selenium] Cloudflare challenge active, waiting %.0fs (%.0fs left)…",
                    poll_interval, remaining)
        time.sleep(poll_interval)

    # Timed out — return whatever we have
    src = driver.page_source
    logger.warning("    [selenium] Cloudflare challenge did NOT clear for %s (page_source: %d chars)",
                   url, len(src))
    return src


def _enrich_with_selenium(articles: list[dict]) -> list[dict]:
    """
    For articles flagged needs_full_content=True, open each URL in a headless
    browser (undetected-chromedriver) and extract the full text with trafilatura.
    A 2-second pause is inserted between page loads to avoid rate limiting.
    One driver instance is reused for the whole batch.
    """
    to_fetch = [a for a in articles if a.get("needs_full_content")]
    if not to_fetch:
        for a in articles:
            a.pop("needs_full_content", None)
        return articles

    logger.info("  Fetching full content for %d article(s) via headless browser…", len(to_fetch))
    driver = None
    try:
        driver = _make_uc_driver()
        logger.info("  Headless browser started OK")
        for article in to_fetch:
            url = article["url"]
            before_len = len(article["content"])
            try:
                logger.info("    [selenium] Loading: %s", url)
                driver.get(url)
                time.sleep(2)  # initial load pause
                page_source = _wait_past_cloudflare(driver, url)
                page_len = len(page_source)
                logger.info("    [selenium] page_source length: %d chars", page_len)
                text = trafilatura.extract(
                    page_source,
                    include_comments=False,
                    include_tables=False,
                )
                after_len = len(text) if text else 0
                logger.info(
                    "    [selenium] trafilatura extracted: %d chars (was %d) — %s",
                    after_len, before_len,
                    "UPDATED" if text and after_len > before_len else "no improvement",
                )
                if text and after_len > before_len:
                    article["content"] = text
            except Exception as exc:
                logger.warning("    [selenium] Browser fetch failed for %s: %s", url, exc)
    except Exception as exc:
        logger.error("Could not start headless browser: %s", exc)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    for a in articles:
        a.pop("needs_full_content", None)
    return articles


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------

def fetch_rss(source_url: str, known_urls: set[str] | None = None, date_from: str | None = None) -> list[dict]:
    """Fetch and parse an RSS/Atom feed. Returns list of article dicts.

    date_from: ISO date string; articles published before this date are skipped.
               Falls back to DATE_RANGE_DAYS from config when omitted.
    Articles already in known_urls are skipped (no HTTP call).
    Short-content articles are enriched via a headless browser.
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

    # Enrich short-content articles with full page text via headless browser
    articles = _enrich_with_selenium(articles)

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
