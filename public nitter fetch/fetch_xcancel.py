"""
Standalone script: fetch all tweets from @aleabitoreddit on xcancel.com
going back 12 months, using Playwright to mimic human browsing.

Output files (written beside this script):
  aleabitoreddit_tweets.json  — all collected tweet dicts (appended each page)
  aleabitoreddit_state.json   — resume cursor + progress counters
  aleabitoreddit_fetch.log    — rotating log (5 MB × 3 backups)

Usage:
  python fetch_xcancel.py

Resume:
  Just re-run the same command. State is loaded automatically.

Stop condition:
  Tweets older than 12 months from today, OR no more pages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HANDLE = "aleabitoreddit"
BASE_URL = "https://xcancel.com"
PROFILE_URL = f"{BASE_URL}/{HANDLE}"

CUTOFF: datetime = datetime.now(timezone.utc) - timedelta(days=365)

MIN_DELAY = 120   # seconds between pages (lower bound)
MAX_DELAY = 240   # seconds between pages (upper bound)
MAX_RETRIES = 3   # consecutive page-load failures before giving up

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HERE = Path(__file__).parent
TWEETS_FILE = HERE / f"{HANDLE}_tweets.json"
STATE_FILE = HERE / f"{HANDLE}_state.json"
LOG_FILE = HERE / f"{HANDLE}_fetch.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("fetch_xcancel")
    log.setLevel(logging.DEBUG)

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    log.addHandler(fh)
    log.addHandler(ch)
    return log


logger = _setup_logging()

# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\w+ \d+, \d{4}) · (\d+:\d+ [AP]M) UTC")


def parse_date(title: str) -> datetime | None:
    m = _DATE_RE.search(title)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d, %Y %I:%M %p")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def to_xcom_url(href: str) -> str:
    href = href.split("#")[0]
    return "https://x.com" + href if href.startswith("/") else href


def extract_tweets(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tweets: list[dict] = []
    for item in soup.select(".timeline-item"):
        if "show-more" in (item.get("class") or []):
            continue
        date_el = item.select_one(".tweet-date a")
        if not date_el:
            continue
        href = date_el.get("href", "")
        if not href:
            continue
        tweet_url = to_xcom_url(href)
        dt = parse_date(date_el.get("title", ""))
        content_el = item.select_one(".tweet-content")
        content = content_el.get_text(separator=" ", strip=True) if content_el else ""
        tweets.append({
            "url": tweet_url,
            "content": content,
            "published_at": dt.isoformat() if dt else None,
            "_dt": dt,
        })
    return tweets


def extract_next_cursor(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(".show-more a"):
        href = el.get("href", "")
        if "cursor=" in href:
            raw = href.split("cursor=")[1].split("&")[0]
            return unquote(raw)
    return None


def is_rate_limited(html: str) -> bool:
    markers = ["rate limit", "429", "too many requests", "blocked"]
    lower = html.lower()
    return any(m in lower for m in markers)

# ---------------------------------------------------------------------------
# State / persistence helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            logger.info("Resuming from state: page=%d, tweets=%d, cursor=%s",
                        state.get("page", 0), state.get("tweet_count", 0),
                        state.get("cursor") or "start")
            return state
        except Exception as exc:
            logger.warning("Could not load state file (%s) — starting fresh.", exc)
    return {"cursor": None, "page": 0, "tweet_count": 0, "oldest_seen": None}


def save_state(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug("State saved: page=%d, tweets=%d", state["page"], state["tweet_count"])


def load_seen_urls() -> set[str]:
    if TWEETS_FILE.exists():
        try:
            data = json.loads(TWEETS_FILE.read_text(encoding="utf-8"))
            urls = {t["url"] for t in data if "url" in t}
            logger.info("Loaded %d existing tweet URLs from %s", len(urls), TWEETS_FILE.name)
            return urls
        except Exception as exc:
            logger.warning("Could not load tweets file (%s) — seen-set empty.", exc)
    return set()


def append_tweets(new_tweets: list[dict]) -> None:
    existing: list[dict] = []
    if TWEETS_FILE.exists():
        try:
            existing = json.loads(TWEETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.extend(new_tweets)
    TWEETS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

async def slow_scroll(page: Page) -> None:
    """Scroll to the bottom slowly in random steps to mimic human reading."""
    total_height: int = await page.evaluate("document.body.scrollHeight")
    current = 0
    step = random.randint(150, 300)
    while current < total_height:
        current = min(current + step, total_height)
        await page.evaluate(f"window.scrollTo(0, {current})")
        await asyncio.sleep(random.uniform(0.1, 0.4))
        step = random.randint(150, 300)
    # Small pause after reaching the bottom
    await asyncio.sleep(random.uniform(0.5, 1.2))


async def human_pause(lo: float = 0.3, hi: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def fetch_page_html(page: Page, url: str) -> str | None:
    """Navigate to url, wait for network idle, slow-scroll, return page HTML."""
    logger.debug("Navigating to: %s", url)
    try:
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        logger.warning("Timeout waiting for networkidle on %s", url)
    except Exception as exc:
        logger.error("Navigation error for %s: %s", url, exc)
        return None

    await human_pause(0.8, 2.0)
    await slow_scroll(page)
    html = await page.content()
    logger.debug("Page HTML length: %d chars", len(html))
    return html


async def click_load_more(page: Page, cursor: str) -> bool:
    """Try to click the 'Load more' link. Returns True if clicked."""
    try:
        load_more = page.locator(".show-more a").last
        count = await load_more.count()
        if count == 0:
            return False
        href = await load_more.get_attribute("href") or ""
        if "cursor=" not in href:
            return False
        await load_more.hover()
        await human_pause(0.4, 0.9)
        await load_more.click()
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await human_pause(0.5, 1.5)
        return True
    except Exception as exc:
        logger.debug("click_load_more failed (%s), will navigate directly.", exc)
        return False

# ---------------------------------------------------------------------------
# Countdown sleep
# ---------------------------------------------------------------------------

async def countdown_sleep(seconds: float) -> None:
    logger.info("Waiting %.0f seconds before next page…", seconds)
    remaining = seconds
    while remaining > 0:
        chunk = min(30.0, remaining)
        await asyncio.sleep(chunk)
        remaining -= chunk
        if remaining > 0:
            logger.debug("  …%.0f seconds remaining", remaining)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=" * 60)
    logger.info("fetch_xcancel.py  —  @%s  —  cutoff: %s", HANDLE, CUTOFF.date())
    logger.info("=" * 60)

    state = load_state()
    seen_urls = load_seen_urls()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # visible for debugging
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=USER_AGENT,
            locale="en-US",
        )
        page = await context.new_page()

        cursor: str | None = state.get("cursor")
        page_num: int = state.get("page", 0)
        tweet_count: int = state.get("tweet_count", 0)
        oldest_seen: str | None = state.get("oldest_seen")
        consecutive_failures = 0

        while True:
            page_num += 1
            url = PROFILE_URL if cursor is None else f"{PROFILE_URL}?cursor={cursor}"
            logger.info(
                "─── Page %d  cursor=%s  tweets_so_far=%d",
                page_num, cursor or "start", tweet_count,
            )

            # Fetch with retry
            html: str | None = None
            for attempt in range(1, MAX_RETRIES + 1):
                html = await fetch_page_html(page, url)
                if html is not None:
                    break
                wait = 60 * attempt
                logger.warning("Attempt %d/%d failed. Retrying in %ds…", attempt, MAX_RETRIES, wait)
                await asyncio.sleep(wait)

            if html is None:
                consecutive_failures += 1
                logger.error(
                    "Page %d failed after %d attempts. Consecutive failures: %d",
                    page_num, MAX_RETRIES, consecutive_failures,
                )
                if consecutive_failures >= 3:
                    logger.critical("3 consecutive failures — saving state and exiting.")
                    save_state({
                        "cursor": cursor, "page": page_num - 1,
                        "tweet_count": tweet_count, "oldest_seen": oldest_seen,
                    })
                    break
                continue
            else:
                consecutive_failures = 0

            # Check for rate limiting
            if is_rate_limited(html):
                logger.warning("Rate limit detected on page %d. Sleeping 5 minutes…", page_num)
                await asyncio.sleep(300)
                page_num -= 1  # retry same page
                continue

            # Extract tweets
            raw_tweets = extract_tweets(html)
            logger.info("Page %d: found %d tweet elements in HTML", page_num, len(raw_tweets))

            if not raw_tweets:
                logger.info("No tweets on page %d — end of timeline.", page_num)
                break

            new_tweets: list[dict] = []
            stop_flag = False
            oldest_dt_this_page: datetime | None = None

            for t in raw_tweets:
                url_tweet = t["url"]
                dt: datetime | None = t.pop("_dt", None)

                if url_tweet in seen_urls:
                    logger.debug("Skipping duplicate: %s", url_tweet)
                    continue

                if dt is None:
                    logger.debug("No date parsed for %s — keeping.", url_tweet)
                elif dt < CUTOFF:
                    logger.info(
                        "Tweet dated %s is before cutoff %s — stopping pagination.",
                        dt.date(), CUTOFF.date(),
                    )
                    stop_flag = True
                    break

                seen_urls.add(url_tweet)
                new_tweets.append({k: v for k, v in t.items() if k != "_dt"})

                if dt and (oldest_dt_this_page is None or dt < oldest_dt_this_page):
                    oldest_dt_this_page = dt

            if new_tweets:
                append_tweets(new_tweets)
                tweet_count += len(new_tweets)
                if oldest_dt_this_page:
                    oldest_seen = oldest_dt_this_page.isoformat()

            logger.info(
                "Page %d done: +%d new tweets (total %d), oldest this page: %s",
                page_num, len(new_tweets), tweet_count,
                oldest_dt_this_page.date() if oldest_dt_this_page else "unknown",
            )

            # Extract next cursor from HTML before navigating
            next_cursor = extract_next_cursor(html)
            logger.debug("Next cursor: %s", next_cursor or "none")

            # Save state after every page
            save_state({
                "cursor": next_cursor,
                "page": page_num,
                "tweet_count": tweet_count,
                "oldest_seen": oldest_seen,
            })

            if stop_flag:
                logger.info("Reached 12-month cutoff. Done.")
                break

            if not next_cursor:
                logger.info("No next cursor found — end of timeline.")
                break

            # Try clicking the Load more button (human-like), else navigate directly
            clicked = await click_load_more(page, next_cursor)
            if clicked:
                logger.debug("Clicked 'Load more' button.")
            else:
                logger.debug("Will navigate directly to next cursor URL.")

            cursor = next_cursor

            # Gentle delay before next page
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            await countdown_sleep(delay)

            # If we navigated by click, the page is already loaded — skip navigation.
            # We'll re-enter the loop and fetch_page_html will re-navigate using url.
            # That's fine: re-navigating to the same cursor URL is idempotent.

        await context.close()
        await browser.close()

    logger.info("Finished. Total tweets collected: %d", tweet_count)
    logger.info("Tweets saved to: %s", TWEETS_FILE)
    logger.info("State saved to:  %s", STATE_FILE)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. State was last saved after the most recent completed page.")
        sys.exit(0)
