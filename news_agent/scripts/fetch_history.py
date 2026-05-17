"""
Admin-only CLI script: fetch historical tweets from one X.com account via
Nitter HTML pagination.

This script is intentionally NOT exposed via the Flask web UI — run it
directly on the server as root/admin only.

Usage:
    python scripts/fetch_history.py @elonmusk --from 2025-01-01 --to 2025-03-31
    python scripts/fetch_history.py @karpathy --from 2025-01-01 --to 2025-06-01 --delay 180
    python scripts/fetch_history.py @sama --from 2025-01-01 --to 2025-06-01 --source-id 3

Options:
    handle          X.com handle, with or without leading @
    --from          Start date (inclusive), YYYY-MM-DD
    --to            End date (inclusive), YYYY-MM-DD
    --delay         Seconds between page fetches (default: 120)
    --source-id     DB source ID to store tweets under.
                    If omitted, auto-detected from the nitter sources in the DB.
    --dry-run       Print what would be stored without writing to DB.
"""

from __future__ import annotations

import argparse
import logging
import sys
import random
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from fetchers.nitter_html import fetch_nitter_html_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _find_source_id(handle: str) -> int | None:
    """Look up the DB source ID for a nitter handle."""
    handle_clean = handle.lower().lstrip("@")
    for source in db.get_all_sources():
        if source["type"] == "nitter":
            stored = source["url"].replace("nitter:", "").lstrip("@").lower()
            if stored == handle_clean:
                return source["id"]
    return None


def fetch_history(
    handle: str,
    date_from: str,
    date_to: str,
    delay: int,
    source_id: int,
    dry_run: bool = False,
) -> int:
    """
    Paginate through Nitter HTML pages for `handle`, storing tweets whose
    published_at falls within [date_from, date_to].

    Returns the number of new tweets stored (0 in dry-run mode).
    """
    handle = handle.lstrip("@")
    start = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)

    logger.info(
        "Fetching history: @%s  %s → %s  (delay=%ds, dry_run=%s)",
        handle, date_from, date_to, delay, dry_run,
    )

    total_new = 0
    page_num = 0
    cursor: str | None = None

    while True:
        page_num += 1
        logger.info("Page %d for @%s (cursor=%s)…", page_num, handle, cursor or "start")

        tweets, next_cursor = fetch_nitter_html_page(handle, cursor=cursor)

        if not tweets:
            logger.info("Empty page — stopping.")
            break

        stop_pagination = False
        for tweet in tweets:
            pub = tweet.get("published_at")
            if not pub:
                continue

            dt = datetime.fromisoformat(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt > end:
                continue  # newer than end date — skip, keep paginating

            if dt < start:
                logger.info("Reached %s (before %s) — stopping.", dt.date(), date_from)
                stop_pagination = True
                break

            # Tweet is within the requested range
            if dry_run:
                logger.info("[dry-run] would store: %s  %s", dt.date(), tweet["url"])
            else:
                inserted = db.insert_article(
                    source_id=source_id,
                    title=tweet["title"],
                    url=tweet["url"],
                    content=tweet["content"],
                    published_at=pub,
                )
                if inserted:
                    total_new += 1
                    logger.info("Stored: %s  %s", dt.date(), tweet["url"])

        if stop_pagination or not next_cursor:
            break

        cursor = next_cursor
        jitter = random.randint(1, 60)
        actual = delay + jitter
        logger.info("Waiting %ds (%d base + %d jitter) before next page…", actual, delay, jitter)
        time.sleep(actual)

    logger.info("Done. %d new tweet(s) stored.", total_new)
    return total_new


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch historical tweets via Nitter HTML pagination (admin only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("handle", help="X.com handle (e.g. @elonmusk or elonmusk)")
    parser.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD",
                        help="Start date (inclusive)")
    parser.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD",
                        help="End date (inclusive)")
    parser.add_argument("--delay", type=int, default=120, metavar="SECONDS",
                        help="Seconds between page fetches (default: 120)")
    parser.add_argument("--source-id", type=int, default=None, metavar="ID",
                        help="DB source ID; auto-detected if omitted")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be stored without writing to DB")
    args = parser.parse_args()

    db.init_db()

    source_id = args.source_id
    if source_id is None:
        source_id = _find_source_id(args.handle)
        if source_id is None:
            logger.error(
                "No nitter source found for %s in the DB. "
                "Add the account in the app first, or pass --source-id.",
                args.handle,
            )
            sys.exit(1)
        logger.info("Auto-detected source_id=%d for %s", source_id, args.handle)

    fetch_history(
        handle=args.handle,
        date_from=args.date_from,
        date_to=args.date_to,
        delay=args.delay,
        source_id=source_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
