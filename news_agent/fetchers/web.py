"""Fetcher for company press release / news index pages (no RSS)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import MIN_ARTICLE_DATE

logger = logging.getLogger(__name__)

_MIN_DT = datetime.fromisoformat(MIN_ARTICLE_DATE).replace(tzinfo=timezone.utc)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_TRACKING_QUERY_PREFIXES = ("utm_",)
_ERROR_PAGE_PATTERNS = (
    "404 poem",
    "four-zero-four",
    "returned a 404 error",
    "could not be found",
    "no main article body",
    "bad gateway",
)


def _canonical_article_url(url: str) -> str:
    """Normalize discovered URLs so equivalent links do not get fetched twice."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not any(key.lower().startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(query_items, doseq=True)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _looks_like_error_page(content: str) -> bool:
    text = (content or "").lower()
    return any(pattern in text for pattern in _ERROR_PAGE_PATTERNS)


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if line.strip().startswith("#") and stripped:
            return stripped
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("http://", "https://")):
            return stripped[:180]
    return fallback


def _parse_cutoff(date_from: Optional[str]) -> Optional[datetime]:
    if not date_from:
        return None
    try:
        return datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_too_old(date_str: str, cutoff: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < cutoff
    except Exception:
        return False


def _is_too_new(date_str: str, ceiling: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > ceiling
    except Exception:
        return False


async def _agent_discover_links(
    index_url: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """
    Use a browser-use LLM agent to discover article links on a news index page.
    Returns a list of {"url": str, "date": str|None} dicts.
    """
    from browser_use import Agent
    from browser_use.browser.profile import BrowserProfile
    from fetchers.browser_use_fetcher import _make_qwen_llm

    if date_from and date_to:
        date_hint = f" Only include articles published between {date_from} and {date_to} (inclusive)."
    elif date_from:
        date_hint = f" Only include articles published on or after {date_from}."
    else:
        date_hint = ""

    task = (
        f"Navigate to {index_url}. This is a news or press release listing page. "
        "Your job is to find links to individual news articles, blog posts, or press releases. "
        "Exclude: navigation menus, footer links, social media links, links to site sections "
        "(About, Careers, Research, Products, etc.), and links that are not individual articles. "
        f"{date_hint}"
        "Return a JSON array where each element has exactly two fields: "
        '"url" (full absolute URL of the article) and '
        '"date" (publication date in YYYY-MM-DD format as shown on the page, or null if not visible). '
        'Example output: [{"url": "https://example.com/news/article-1", "date": "2026-05-10"}, ...]. '
        "Call done() with ONLY the raw JSON array — no markdown, no explanation."
    )

    profile = BrowserProfile(
        args=[
            "--lang=en-US",
            "--accept-lang=en-US",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
        headless=True,
    )
    agent = Agent(task=task, llm=_make_qwen_llm(), use_vision=True, browser_profile=profile)
    result = await agent.run(max_steps=5)

    raw = result.final_result() or ""
    if not raw:
        parts = result.extracted_content() or []
        raw = max(parts, key=len) if parts else ""

    # Extract JSON array from response (agent may wrap it in markdown code fences)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        logger.warning("[web agent] no JSON array found in response: %s", raw[:300])
        return []
    try:
        items = json.loads(match.group())
        valid = []
        seen_urls = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _canonical_article_url(item.get("url", ""))
            if not url.startswith("http") or url in seen_urls:
                continue
            seen_urls.add(url)
            valid.append({"url": url, "date": item.get("date")})
        logger.info("[web agent] discovered %d article link(s) from %s", len(valid), index_url)
        return valid
    except Exception as exc:
        logger.warning("[web agent] JSON parse failed: %s | raw: %s", exc, raw[:300])
        return []


def fetch_web(
    index_url: str,
    known_urls: set[str],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """
    Scrape a company news/press release index page using an LLM agent for link
    discovery, then Playwright (+ agent fallback) for individual article content.

    index_url:  URL of the news listing page.
    known_urls: article URLs already in the DB (skipped entirely).
    date_from:  ISO date string; articles older than this are skipped.
    date_to:    ISO date string; articles newer than this are skipped.

    Date handling:
      - Listing-page date (from LLM agent): used as a cheap pre-filter only.
        If clearly out of range, the article is skipped before a browser call.
      - Article-page date (from trafilatura metadata): authoritative.
        Extracted for free during the Playwright fetch and used for the final
        date check and as the stored published_at value.
      - If both dates are unavailable, the article is kept (conservative).
    """
    from fetchers.browser_use_fetcher import fetch_article_with_meta

    logger.info("Fetching web index: %s", index_url)

    link_items = asyncio.run(_agent_discover_links(index_url, date_from, date_to))
    if not link_items:
        return []

    cutoff = _parse_cutoff(date_from) or _MIN_DT
    ceiling = _parse_cutoff(date_to)
    articles = []
    skipped_known = skipped_date = skipped_error = 0

    seen_urls = {_canonical_article_url(url) for url in known_urls}

    for item in link_items:
        url = _canonical_article_url(item.get("url", ""))
        listing_date = item.get("date") or None

        if not url:
            continue
        if url in seen_urls:
            skipped_known += 1
            continue
        seen_urls.add(url)

        # Cheap pre-filter: skip without a browser call when the listing-page
        # date is present and already clearly outside the target range.
        if listing_date:
            if _is_too_old(listing_date, cutoff):
                skipped_date += 1
                continue
            if ceiling and _is_too_new(listing_date, ceiling):
                skipped_date += 1
                continue

        # Fetch full content + extract authoritative date from the article page itself
        try:
            content, article_date = fetch_article_with_meta(url)
        except Exception as exc:
            logger.warning("  fetch_article_with_meta failed for %s: %s", url, exc)
            continue

        if not content:
            continue
        if _looks_like_error_page(content):
            skipped_error += 1
            logger.warning("  skipping %s - extracted content looks like an error page", url)
            continue
        # Article-page date is authoritative; fall back to listing-page date.
        # If the article-page date is implausibly older than the listing date
        # (>30 days), trafilatura likely picked up a stale embedded date —
        # distrust it and prefer the listing date.
        if article_date and listing_date:
            try:
                a_dt = datetime.fromisoformat(article_date)
                l_dt = datetime.fromisoformat(listing_date)
                if a_dt.tzinfo is None:
                    a_dt = a_dt.replace(tzinfo=timezone.utc)
                if l_dt.tzinfo is None:
                    l_dt = l_dt.replace(tzinfo=timezone.utc)
                if (l_dt - a_dt).days > 30:
                    logger.warning(
                        "  article-page date %s is >30 days older than listing date %s for %s — using listing date",
                        article_date, listing_date, url,
                    )
                    article_date = None
            except Exception:
                pass

        final_date = article_date or listing_date

        if article_date and article_date != listing_date and listing_date:
            logger.info(
                "  date corrected for %s: listing=%s article=%s",
                url, listing_date, article_date,
            )

        # Final date check against the authoritative article-page date
        if final_date:
            if _is_too_old(final_date, cutoff):
                skipped_date += 1
                logger.info("  skipping %s — article date %s is out of range", url, final_date)
                continue
            if ceiling and _is_too_new(final_date, ceiling):
                skipped_date += 1
                continue

        title = _extract_title(content, url)

        articles.append({
            "title": title,
            "url": url,
            "content": content,
            "published_at": final_date,
        })

    if skipped_known:
        logger.info("  Skipped %d already-known article(s)", skipped_known)
    if skipped_date:
        logger.info("  Skipped %d article(s) outside date range", skipped_date)
    if skipped_error:
        logger.info("  Skipped %d error page(s)", skipped_error)
    logger.info("  → %d new articles from %s", len(articles), index_url)
    return articles
