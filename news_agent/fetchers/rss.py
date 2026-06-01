"""Fetcher for RSS/Atom feeds and Nitter (X.com via RSS)."""

from __future__ import annotations

import logging
import re
import random
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
    NITTER_FETCH_PERIOD_HOURS,
    NITTER_INSTANCES,
    NITTER_PAGE_DELAY,
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

def _body_to_content(body_html: str, has_images: bool) -> str:
    """
    Convert an RSS body to storable content.
    - With images: convert HTML → Markdown (preserves <img> tags from the feed).
    - Text-only: strip HTML tags and return plain text.
    """
    if has_images:
        from bs4 import BeautifulSoup
        from fetchers.browser_use_fetcher import _soup_to_markdown
        soup = BeautifulSoup(body_html, "html.parser")
        return _soup_to_markdown(soup).strip()
    return re.sub(r"<[^>]+>", " ", body_html).strip()


def _entry_to_article_basic(entry) -> dict:
    """
    Parse RSS entry fields.

    Deciding whether to visit the original URL via Playwright:
    - If the RSS body contains images (<img tags): convert HTML→Markdown inline;
      no browser visit needed — images come from the feed itself.
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
    content = _body_to_content(body, has_images)
    body_plain = re.sub(r"<[^>]+>", " ", body).strip()
    published_at = _parse_published(entry)

    # Only needs browser enrichment for text-only short feeds; image feeds are
    # already fully converted from the RSS body HTML above.
    needs_full = (not has_images) and len(body_plain) < CONTENT_LENGTH_THRESHOLD and bool(link)

    return {
        "title": title,
        "url": link,
        "content": content,
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


def fetch_youtube(feed_url: str, known_urls: set[str] | None = None, date_from: str | None = None) -> list[dict]:
    """
    Fetch a YouTube channel RSS feed. Like fetch_rss but never triggers
    Playwright enrichment — YouTube watch pages yield no useful text content.
    The video description comes from the feed's <media:description> element,
    which YouTube includes in full (not truncated) in its Atom feeds.
    """
    cutoff = _parse_cutoff(date_from)
    logger.info("Fetching YouTube RSS: %s", feed_url)
    try:
        feed = feedparser.parse(feed_url)
    except Exception as exc:
        logger.error("feedparser error for %s: %s", feed_url, exc)
        return []

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

        title = getattr(entry, "title", "") or ""
        # YouTube Atom feeds expose the full video description in <media:description>
        # which feedparser maps to entry.summary
        description = ""
        if hasattr(entry, "summary") and entry.summary:
            description = re.sub(r"<[^>]+>", " ", entry.summary).strip()

        articles.append({
            "title": title,
            "url": link,
            "content": description,
            "published_at": pub,
            "needs_full_content": False,
        })

    if skipped_known:
        logger.info("  Skipped %d already-known YouTube entries", skipped_known)
    logger.info("  → %d YouTube videos from %s", len(articles), feed_url)
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


def fetch_nitter_hybrid(
    handle: str,
    known_urls: set[str] | None = None,
    date_from: str | None = None,
    page_delay: int = NITTER_PAGE_DELAY,
) -> list[dict]:
    """
    Hybrid Nitter fetch: RSS first, then HTML pagination only when needed.

    The fetch window is NITTER_FETCH_PERIOD_HOURS (e.g. 24h or 12h).
    Pagination triggers only when all RSS tweets fall within that window,
    meaning the RSS page is "full" of in-window tweets and there may be more.
    Pagination stops as soon as a tweet is older than the window.

    If date_from is provided (manual fetch with date filter), it takes
    precedence over the period-based cutoff.
    """
    from fetchers.nitter_html import fetch_nitter_html_page

    now = datetime.now(timezone.utc)

    # Cutoff: use explicit date_from if given, otherwise now minus the fetch period
    if date_from:
        period_cutoff = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    else:
        period_cutoff = now - timedelta(hours=NITTER_FETCH_PERIOD_HOURS)

    # Step 1 — fast RSS fetch
    articles = fetch_nitter(handle, known_urls=known_urls, date_from=date_from)

    if not articles:
        # RSS failed (rate-limited / 404) — try HTML page which is Redis-cached by Nitter
        # and does not trigger a fresh X.com API call.
        logger.info("[nitter-hybrid] @%s: RSS empty, trying HTML fallback", handle)
        page_tweets, _ = fetch_nitter_html_page(handle)
        if not page_tweets:
            logger.warning("[nitter-hybrid] @%s: HTML fallback also empty", handle)
            return []
        result = []
        for tweet in page_tweets:
            if known_urls and tweet["url"] in known_urls:
                continue
            if tweet.get("published_at") and _is_too_old(tweet["published_at"], period_cutoff):
                continue
            result.append(tweet)
        logger.info("[nitter-hybrid] @%s: HTML fallback → %d tweets", handle, len(result))
        return result

    # Step 2 — find the oldest tweet's datetime
    dated = [a for a in articles if a.get("published_at")]
    if not dated:
        return articles

    def _as_utc(iso: str) -> datetime:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    oldest_dt = min(_as_utc(a["published_at"]) for a in dated)

    if oldest_dt <= period_cutoff:
        logger.info(
            "[nitter-hybrid] @%s: oldest tweet %s is outside the %dh window — RSS sufficient",
            handle, oldest_dt.strftime("%Y-%m-%d %H:%M"), NITTER_FETCH_PERIOD_HOURS,
        )
        return articles

    # All RSS tweets are within the window — paginate to collect the rest
    logger.info(
        "[nitter-hybrid] @%s: all %d RSS tweets within the %dh window, paginating…",
        handle, len(articles), NITTER_FETCH_PERIOD_HOURS,
    )

    existing_urls: set[str] = {a["url"] for a in articles}
    if known_urls:
        existing_urls |= known_urls

    # Fetch HTML page 1 to get the cursor (tweets overlap with RSS — deduped via existing_urls)
    pre_gap = 10 + random.randint(0, 10)  # 10–20s
    logger.info("[nitter-hybrid] @%s: waiting %ds before HTML page 1…", handle, pre_gap)
    time.sleep(pre_gap)
    _, cursor = fetch_nitter_html_page(handle)

    while cursor:
        jitter = random.randint(1, 60)
        actual_delay = page_delay + jitter
        logger.info("[nitter-hybrid] @%s: waiting %ds (%d + %d jitter) before next page…",
                    handle, actual_delay, page_delay, jitter)
        time.sleep(actual_delay)

        page_tweets, cursor = fetch_nitter_html_page(handle, cursor=cursor)

        if not page_tweets:
            break

        past_window = False
        for tweet in page_tweets:
            if tweet["url"] in existing_urls:
                continue

            pub = tweet.get("published_at")
            if pub:
                tweet_dt = _as_utc(pub)
                if tweet_dt <= period_cutoff:
                    logger.info(
                        "[nitter-hybrid] @%s: tweet at %s is older than %dh window — stopping",
                        handle, tweet_dt.strftime("%Y-%m-%d %H:%M"), NITTER_FETCH_PERIOD_HOURS,
                    )
                    past_window = True
                    break

            articles.append(tweet)
            existing_urls.add(tweet["url"])

        if past_window:
            break

    logger.info("[nitter-hybrid] @%s: %d total tweets after pagination", handle, len(articles))
    return articles
