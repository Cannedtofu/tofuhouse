"""
Shared fetch + summarize pipeline used by both main.py (CLI) and app.py (web).
"""

from __future__ import annotations

import logging
import random
import time

import db
from config import MIN_CONTENT_WORDS, NITTER_INTER_SOURCE_DELAY
from fetchers.rss import fetch_nitter_hybrid, fetch_rss, fetch_youtube
from fetchers.web import fetch_web
from article_summarizer import summarize_new_articles

logger = logging.getLogger(__name__)


def run_fetch_and_summarize(
    summarize: bool = False,
    date_from: str | None = None,
    date_to: str | None = None,
    source_ids: list[int] | None = None,
    source_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
) -> dict:
    """
    Fetch new articles for all sources (or a filtered subset) and store them.
    date_from:     ISO date string; articles older than this are skipped.
    source_ids:    list of source IDs to fetch; None means all sources.
    source_types:  allowlist of types to include, e.g. ["nitter"]; None means all.
    exclude_types: denylist of types to skip, e.g. ["nitter"] for UI fetch.
    Returns a dict: {"total_new": int, "sources": [{"name", "type", "new", "fetched", "error"}]}
    """
    sources = db.get_all_sources()
    if source_ids:
        sources = [s for s in sources if s["id"] in source_ids]
    if source_types:
        sources = [s for s in sources if s["type"] in source_types]
    if exclude_types:
        sources = [s for s in sources if s["type"] not in exclude_types]
    if not sources:
        logger.warning(
            "No sources configured. (source_ids=%s, source_types=%s, exclude_types=%s)",
            source_ids, source_types, exclude_types,
        )
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
            all_stored = db.get_articles(source_ids=[source_id])

            if source_type in ("nitter", "youtube", "xiaoyuzhou"):
                # Short by nature — never Playwright-enrich. Treat all stored
                # URLs as known so already-seen entries are always skipped.
                existing_urls = {row["url"] for row in all_stored}
            else:
                # For RSS/web: re-fetch articles whose stored content is too thin,
                # since Playwright may be able to extract the full body.
                existing_urls = {
                    row["url"]
                    for row in all_stored
                    if len((row["content"] or "").split()) >= MIN_CONTENT_WORDS
                }
                thin_count = sum(
                    1 for row in all_stored
                    if len((row["content"] or "").split()) < MIN_CONTENT_WORDS
                )
                if thin_count:
                    logger.info("  %d existing article(s) have thin content — will re-fetch", thin_count)

            if source_type in ("rss", "youtube"):
                if source_type == "youtube":
                    articles = fetch_youtube(source_url, known_urls=existing_urls, date_from=date_from)
                else:
                    articles = fetch_rss(source_url, known_urls=existing_urls, date_from=date_from)

            elif source_type == "nitter":
                handle = source_url.replace("nitter:", "").lstrip("@")
                # Never pass date_from for nitter — always use period-based cutoff.
                # Historical nitter fetch is admin-only via scripts/fetch_history.py.
                articles = fetch_nitter_hybrid(handle, known_urls=existing_urls)

            elif source_type == "web":
                articles = fetch_web(source_url, known_urls=existing_urls, date_from=date_from, date_to=date_to)

            elif source_type == "xiaoyuzhou":
                from fetchers.xiaoyuzhou import fetch_xiaoyuzhou
                articles = fetch_xiaoyuzhou(source_url, known_urls=existing_urls, date_from=date_from)

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

        # Randomised inter-account gap for nitter sources. Jitter prevents
        # the fixed-interval fingerprint that automation detection looks for.
        if source_type == "nitter" and source != sources[-1]:
            jitter = random.randint(0, NITTER_INTER_SOURCE_DELAY // 2)
            gap = NITTER_INTER_SOURCE_DELAY + jitter
            logger.info("[nitter] inter-account gap: %ds", gap)
            time.sleep(gap)

    if summarize:
        summarize_new_articles()

    return {"total_new": total_new, "sources": source_results}
