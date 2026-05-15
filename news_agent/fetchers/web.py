"""Fetcher for company press release / news index pages (no RSS)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from config import MIN_ARTICLE_DATE

logger = logging.getLogger(__name__)

_MIN_DT = datetime.fromisoformat(MIN_ARTICLE_DATE).replace(tzinfo=timezone.utc)


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
        args=["--lang=en-US", "--accept-lang=en-US"],
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
        valid = [i for i in items if isinstance(i, dict) and i.get("url", "").startswith("http")]
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
    date_from:  ISO date string; articles with a known date older than this are skipped.
    date_to:    ISO date string; articles with a known date newer than this are skipped.
    """
    from fetchers.browser_use_fetcher import fetch_article

    logger.info("Fetching web index: %s", index_url)

    link_items = asyncio.run(_agent_discover_links(index_url, date_from, date_to))
    if not link_items:
        return []

    cutoff = _parse_cutoff(date_from) or _MIN_DT
    ceiling = _parse_cutoff(date_to)
    articles = []
    skipped_known = skipped_date = 0

    for item in link_items:
        url = item.get("url", "").strip().rstrip("/")
        date_str = item.get("date") or None

        if not url:
            continue
        if url in known_urls:
            skipped_known += 1
            continue
        if date_str and _is_too_old(date_str, cutoff):
            skipped_date += 1
            continue
        if date_str and ceiling and _is_too_new(date_str, ceiling):
            skipped_date += 1
            continue

        try:
            content = fetch_article(url)
        except Exception as exc:
            logger.warning("  fetch_article failed for %s: %s", url, exc)
            continue

        if not content:
            continue

        # Extract title from first heading in the markdown content
        title = url
        for line in content.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if line.strip().startswith("#") and stripped:
                title = stripped
                break

        articles.append({
            "title": title,
            "url": url,
            "content": content,
            "published_at": date_str,
        })

    if skipped_known:
        logger.info("  Skipped %d already-known article(s)", skipped_known)
    if skipped_date:
        logger.info("  Skipped %d article(s) outside date range", skipped_date)
    logger.info("  → %d new articles from %s", len(articles), index_url)
    return articles
