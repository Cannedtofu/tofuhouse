"""Fetcher for company press release / news index pages (no RSS)."""

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

from config import MIN_ARTICLE_DATE

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# CSS class/id fragments that typically indicate article link containers
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

    # Look for <a> tags inside likely containers first
    for tag in soup.find_all(True):
        cls = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        if _ARTICLE_HINTS.search(cls) or _ARTICLE_HINTS.search(tag_id):
            for a in tag.find_all("a", href=True):
                href = urljoin(base_url, a["href"])
                if href not in seen and _same_origin(base_url, href) and href != base_url:
                    seen.add(href)
                    results.append(href)

    # Fallback: all <a> tags with meaningful href (not anchors / js)
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


def _fetch_article_content(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text:
                return text
    except Exception as exc:
        logger.warning("trafilatura failed for %s: %s", url, exc)
    return ""


def fetch_web(index_url: str, known_urls: set[str]) -> list[dict]:
    """
    Scrape a company news/press release index page.
    known_urls: set of article URLs already in the DB (skip them).
    Returns list of article dicts.
    """
    logger.info("Fetching web index: %s", index_url)
    try:
        resp = requests.get(index_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch %s: %s", index_url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = _candidate_links(soup, index_url)
    logger.info("  Found %d candidate links", len(links))

    articles = []
    for link in links:
        if link in known_urls:
            continue
        content = _fetch_article_content(link)
        if not content:
            continue

        # Try to extract a title from the page
        try:
            page_resp = requests.get(link, headers=_HEADERS, timeout=10)
            page_soup = BeautifulSoup(page_resp.text, "html.parser")
            title_tag = page_soup.find("h1") or page_soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else link
        except Exception:
            title = link

        articles.append({
            "title": title,
            "url": link,
            "content": content,
            "published_at": None,  # press release pages rarely expose structured dates
        })

    logger.info("  → %d new articles from %s", len(articles), index_url)
    return articles
