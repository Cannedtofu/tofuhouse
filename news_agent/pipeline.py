"""
Shared fetch + summarize pipeline used by both main.py (CLI) and app.py (web).
"""

from __future__ import annotations

import logging

import db
from config import MIN_CONTENT_WORDS
from fetchers.rss import fetch_nitter, fetch_rss
from fetchers.web import fetch_web
from summarizer import summarize_new_articles

logger = logging.getLogger(__name__)


def run_fetch_and_summarize(summarize: bool = False, date_from: str | None = None) -> dict:
    """
    For every source in the DB, fetch new articles and store them.
    date_from: ISO date string (e.g. '2026-04-10'); articles older than this are skipped.
               Falls back to DATE_RANGE_DAYS from config when not provided.
    Already-known article URLs are passed to fetchers so they skip redundant HTTP calls.
    Returns a dict: {"total_new": int, "sources": [{"name", "type", "new", "fetched", "error"}]}
    """
    sources = db.get_all_sources()
    if not sources:
        logger.warning("No sources configured.")
        return {"total_new": 0, "sources": []}

    total_new = 0
    source_results = []

    for source in sources:
        source_id = source["id"]
        source_type = source["type"]
        source_url = source["url"]
        source_name = source["name"]
        url_filter = source["url_filter"] if source["url_filter"] else None

        logger.info("Processing source: %s (%s)", source_name, source_type)
        result = {"name": source_name, "type": source_type, "new": 0, "fetched": 0, "error": None}

        try:
            # Only treat an article as "known" (skip it) when its stored content
            # is already rich enough.  Thin articles are re-fetched so Selenium
            # can fill in their full content.
            all_stored = db.get_articles(source_ids=[source_id])
            existing_urls = {
                row["url"]
                for row in all_stored
                if len((row["content"] or "").split()) >= MIN_CONTENT_WORDS
            }
            thin_urls = {
                row["url"]
                for row in all_stored
                if len((row["content"] or "").split()) < MIN_CONTENT_WORDS
            }
            if thin_urls:
                logger.info(
                    "  %d existing article(s) have thin content (<200 words) — will re-fetch",
                    len(thin_urls),
                )

            if source_type == "rss":
                articles = fetch_rss(source_url, known_urls=existing_urls, date_from=date_from)

            elif source_type == "nitter":
                handle = source_url.replace("nitter:", "").lstrip("@")
                articles = fetch_nitter(handle, known_urls=existing_urls, date_from=date_from)

            elif source_type == "web":
                articles = fetch_web(source_url, known_urls=existing_urls)

            else:
                result["error"] = f"Unknown source type '{source_type}'"
                source_results.append(result)
                continue

            # Apply url_filter: drop articles whose URL doesn't contain the filter string
            if url_filter:
                before = len(articles)
                articles = [a for a in articles if url_filter in a.get("url", "")]
                dropped = before - len(articles)
                if dropped:
                    logger.info("  url_filter '%s' dropped %d article(s)", url_filter, dropped)

            result["fetched"] = len(articles)
            new_count = 0
            for art in articles:
                inserted = db.insert_article(
                    source_id=source_id,
                    title=art["title"],
                    url=art["url"],
                    content=art["content"],
                    published_at=art["published_at"],
                )
                if inserted:
                    new_count += 1

            db.update_source_last_fetched(source_id)
            result["new"] = new_count
            total_new += new_count
            logger.info("  %d fetched, %d new from '%s'", len(articles), new_count, source_name)

        except Exception as exc:
            logger.exception("Error fetching '%s'", source_name)
            result["error"] = str(exc)

        source_results.append(result)

    if summarize:
        summarize_new_articles()

    return {"total_new": total_new, "sources": source_results}
