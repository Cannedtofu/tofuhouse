"""Fetcher for company press release / news index pages (no RSS)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config import MIN_ARTICLE_DATE

logger = logging.getLogger(__name__)

_ARTICLE_HINTS = re.compile(
    r"(news|post|article|blog|press|release|story|update)", re.I
)


def _same_origin(base_url: str, link: str) -> bool:
    base = urlparse(base_url)
    target = urlparse(link)
    return (not target.netloc) or (target.netloc == base.netloc)


def _candidate_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Heuristically find article links on a news index page."""
    seen: set[str] = set()
    results: list[str] = []

    for tag in soup.find_all(True):
        cls = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        if _ARTICLE_HINTS.search(cls) or _ARTICLE_HINTS.search(tag_id):
            for a in tag.find_all("a", href=True):
                href = urljoin(base_url, a["href"])
                if href not in seen and _same_origin(base_url, href) and href != base_url:
                    seen.add(href)
                    results.append(href)

    if not results:
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            if (
                href not in seen
                and _same_origin(base_url, href)
                and href != base_url
                and not href.startswith("javascript")
                and "#" not in href.split("?")[0][-1:]
            ):
                seen.add(href)
                results.append(href)

    return results


async def _fetch_index_html(index_url: str) -> str:
    """Load the news index page with a real browser to handle JS-rendered content."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--lang=en-US"],
        )
        context = await browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        try:
            await page.goto(index_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2_000)
            html = await page.content()
            logger.info("[web] index page HTML: %d chars from %s", len(html), index_url)
        except Exception as exc:
            logger.warning("[web] failed to load index %s: %s", index_url, exc)
            html = ""
        finally:
            await browser.close()
    return html


def fetch_web(index_url: str, known_urls: set[str]) -> list[dict]:
    """
    Scrape a company news/press release index page.
    Uses Playwright to render the index (handles JS-heavy pages), then fetches
    each article URL via browser_use_fetcher.fetch_article() (Playwright first,
    browser-use agent as last resort).

    known_urls: set of article URLs already in the DB (skip them entirely).
    Returns list of article dicts.
    """
    from fetchers.browser_use_fetcher import fetch_article

    logger.info("Fetching web index: %s", index_url)

    html = asyncio.run(_fetch_index_html(index_url))
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = _candidate_links(soup, index_url)
    logger.info("  Found %d candidate links", len(links))

    articles = []
    for link in links:
        if link in known_urls:
            continue

        try:
            content = fetch_article(link)
        except Exception as exc:
            logger.warning("  fetch_article failed for %s: %s", link, exc)
            continue

        if not content:
            continue

        # Extract title from the rendered page cheaply via BeautifulSoup on the
        # same HTML we already have from the index, or fall back to the URL.
        title = link
        try:
            # Re-use the index soup only if the link appears as an <a> with text
            a_tag = soup.find("a", href=True, string=True)
            page_soup = BeautifulSoup(content[:2000], "html.parser")
            h1 = page_soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        except Exception:
            pass

        articles.append({
            "title": title,
            "url": link,
            "content": content,
            "published_at": None,
        })

    logger.info("  → %d new articles from %s", len(articles), index_url)
    return articles
