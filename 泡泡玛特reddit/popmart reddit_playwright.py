from __future__ import annotations

import sys
import pandas as pd
import time
import datetime
from datetime import timezone
import os
import glob
import json
import random
from urllib.parse import quote as url_quote
from playwright.sync_api import sync_playwright

# ========================= Configuration =========================
BASE_URL = "https://old.reddit.com"   # server-rendered HTML, no JS needed for content

KEYWORDS = [
    "popmart", "labubu", "popmart hirono", "popmart skullpanda",
    "popmart peach riot", "popmart twinkle twinkle", "popmart crybaby",
    "popmart molly", "popmart dimoo"
]

SUBREDDITS = [
    "SkullpandaArtDolls", "labubu", "CryBabyDolls", "hirono",
    "peachriot", "PopMartCollectors", "Dimoos", "TwinkleTwinkleCollect"
]

# Playwright accesses live Reddit - 2025 and 2026 data both available!
KEYWORD_MIN_DATE   = datetime.datetime(2025, 1, 1, tzinfo=timezone.utc)
SUBREDDIT_MIN_DATE = datetime.datetime(2025, 1, 1, tzinfo=timezone.utc)
KEYWORD_MIN_TS   = int(KEYWORD_MIN_DATE.timestamp())
SUBREDDIT_MIN_TS = int(SUBREDDIT_MIN_DATE.timestamp())

TEST_MODE         = False
POSTS_PER_KEYWORD = 1 if TEST_MODE else 100   # max posts per keyword
MIN_COMMENTS      = 10   # skip posts with <= this many comments (low engagement)

HEADLESS = True   # set False to watch the browser during debugging

EXISTING_EXCEL_PATTERN = "popmart_v*.xlsx"

# PAGE_DELAY is a *base* - actual wait = PAGE_DELAY + random jitter so requests
# don't arrive at perfectly regular machine intervals (looks more human).
PAGE_DELAY    = 3.5   # base seconds between page navigations
JITTER        = (0.5, 2.0)   # (min, max) extra seconds added randomly each request
SECTION_PAUSE = 12.0  # seconds between keywords / subreddits

# Retry / resilience
MAX_RETRIES    = 3            # attempts per URL before giving up
RETRY_BACKOFF  = [45, 90, 180]  # seconds to wait after attempt 1, 2, 3 failures

# Failed posts are written here so you can retry them in a later run
FAILED_POSTS_FILE = f"failed_posts_{datetime.date.today()}.json"

# To retry posts that failed in a previous run, set this to the saved filename:
#   RETRY_FILE = "failed_posts_2026-06-11.json"
# Leave as "" for a normal full scrape.
RETRY_FILE = ""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# =================================================================


# ---------------------------------------------------------------------------
# JavaScript helpers evaluated inside Playwright
# ---------------------------------------------------------------------------

# --- Subreddit /new/ listings (.thing elements) ---
# data-timestamp is in milliseconds; divide by 1000 for UTC seconds
_LISTING_EXTRACT_JS = """
() => {
    const results = [];
    const things = document.querySelectorAll('.thing[data-type="link"][data-fullname]');
    things.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname.startsWith('t3_')) return;

        const titleEl = el.querySelector('a.title');
        const tsMs    = parseFloat(el.getAttribute('data-timestamp') || '0');

        results.push({
            fullname:     fullname,
            id:           fullname.replace('t3_', ''),
            created_utc:  tsMs / 1000,
            author:       el.getAttribute('data-author') || '[deleted]',
            score:        parseInt(el.getAttribute('data-score') || '0') || 0,
            num_comments: parseInt(el.getAttribute('data-comments-count') || '0'),
            permalink:    el.getAttribute('data-permalink') || '',
            subreddit:    el.getAttribute('data-subreddit') || '',
            title:        titleEl ? titleEl.innerText.trim() : '',
        });
    });
    return results;
}
"""

# --- Keyword search pages (.search-result-link elements) ---
# Requires &type=link in the URL to get post-only results with full 25/page
_SEARCH_EXTRACT_JS = """
() => {
    const results = [];
    const items = document.querySelectorAll('.search-result-link[data-fullname]');
    items.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname.startsWith('t3_')) return;

        // Title & permalink
        const titleEl = el.querySelector('.search-title');
        const title   = titleEl ? titleEl.innerText.trim() : '';
        // Permalink: strip the origin to get path only
        let permalink = '';
        if (titleEl && titleEl.href) {
            try { permalink = new URL(titleEl.href).pathname; } catch(e) {}
        }

        // Timestamp from <time datetime="2026-01-15T12:00:00+00:00">
        const timeEl = el.querySelector('time');
        const dt     = timeEl ? timeEl.getAttribute('datetime') : null;
        const createdUtc = dt ? Date.parse(dt) / 1000 : 0;

        // Score: ".search-score" text is "N points" - parseInt handles "N points" fine
        const scoreEl  = el.querySelector('.search-score');
        const score    = scoreEl ? (parseInt(scoreEl.textContent) || 0) : 0;

        // Comments: ".search-comments" text is "N comments"
        const commentsEl  = el.querySelector('.search-comments');
        const numComments = commentsEl ? (parseInt(commentsEl.textContent) || 0) : 0;

        // Author
        const authorEl = el.querySelector('.search-author .author');
        const author   = authorEl ? authorEl.textContent.trim() : '[deleted]';

        // Subreddit
        const subEl    = el.querySelector('.search-subreddit-link');
        const subreddit = subEl ? subEl.textContent.trim().replace(/^r\//, '') : '';

        results.push({
            fullname:     fullname,
            id:           fullname.replace('t3_', ''),
            created_utc:  createdUtc,
            author:       author,
            score:        score,
            num_comments: numComments,
            permalink:    permalink,
            subreddit:    subreddit,
            title:        title,
        });
    });
    return results;
}
"""

# --- Comments on a post page ---
_COMMENT_EXTRACT_JS = """
() => {
    const results = [];
    // All comment things inside the comment area
    const things = document.querySelectorAll('.commentarea [data-fullname^="t1_"]');

    things.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname) return;

        // Timestamp from <time datetime="...">
        const timeEl = el.querySelector('time');
        const dt     = timeEl ? timeEl.getAttribute('datetime') : null;
        const createdUtc = dt ? Date.parse(dt) / 1000 : 0;

        // Score: .score element has title="N" with the numeric vote count
        const scoreEl = el.querySelector('.score');
        const score   = scoreEl ? (parseInt(scoreEl.getAttribute('title') || '0') || 0) : 0;

        // Body text
        const bodyEl = el.querySelector('.usertext-body .md');
        const body   = bodyEl ? bodyEl.innerText.trim() : '';

        // Parent fullname via DOM walk
        // Walk up from this element; first ancestor with data-fullname that isn't this
        // element = parent comment (t1_) or the post (t3_)
        let parentFullname = '';
        let p = el.parentElement;
        while (p) {
            if (p.classList.contains('commentarea')) {
                // Reached comment-area root -> direct reply to post
                const postEl = document.querySelector('.thing[data-type="link"][data-fullname]');
                parentFullname = postEl ? postEl.getAttribute('data-fullname') : '';
                break;
            }
            const fn = p.getAttribute && p.getAttribute('data-fullname');
            if (fn && fn !== fullname) {
                parentFullname = fn;
                break;
            }
            p = p.parentElement;
        }

        results.push({
            fullname:        fullname,
            parent_fullname: parentFullname,
            author:          el.getAttribute('data-author') || '[deleted]',
            created_utc:     createdUtc,
            score:           score,
            body:            body,
        });
    });
    return results;
}
"""

# Click every "load more comments" / "continue this thread" link on the page,
# wait briefly for the content to inject, then repeat until none remain.
# Old Reddit renders extra comments via synchronous page replacement so a simple
# click + short sleep is enough.
_LOAD_MORE_JS = """
async () => {
    let clicked = 0;
    const MAX_ROUNDS = 8;   // safety cap - avoids infinite loops on huge threads
    for (let round = 0; round < MAX_ROUNDS; round++) {
        // 'morechildren' spans contain the "load more comments" link
        // 'deeplink' anchors say "continue this thread >"
        const links = Array.from(document.querySelectorAll(
            'span.morechildren a.button, .deeplink a'
        ));
        if (links.length === 0) break;
        for (const link of links) {
            link.click();
            clicked++;
            await new Promise(r => setTimeout(r, 800));   // let DOM update
        }
    }
    return clicked;
}
"""

# Self-post body text from a post page
_POST_BODY_JS = """
() => {
    const candidates = [
        '.link .usertext-body .md',
        '.expando .usertext-body .md',
        '.selftext .md',
    ];
    for (const sel of candidates) {
        const el = document.querySelector(sel);
        if (el) return el.innerText.trim();
    }
    return '';
}
"""


# ---------------------------------------------------------------------------
# Logging (ASCII only - compatible with Windows GBK terminal)
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str) -> None:
    # Encode to ASCII so Windows GBK terminal never crashes on emoji / CJK in
    # post titles or body snippets.  Actual data in all_results is kept as full
    # Unicode — only the console output is sanitised here.
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(f"[{_ts()}] {safe}", flush=True)


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class PopmartScraperPlaywright:

    def __init__(self, playwright) -> None:
        self.browser = playwright.chromium.launch(headless=HEADLESS)
        self.context = self.browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
        )
        # Block images, fonts, media and stylesheets - we parse data, not render pages
        self.context.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,mp4,mp3,avif,css}",
            lambda route: route.abort()
        )
        self.page = self.context.new_page()
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.all_results: list[dict] = []
        self.scraped_post_ids: set[str] = set()
        self.failed_posts: list[dict] = []   # posts whose page fetch failed after all retries

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Historical deduplication
    # ------------------------------------------------------------------

    def load_existing_ids(self) -> set[str]:
        existing_ids: set[str] = set()
        pattern = os.path.join(self.base_path, EXISTING_EXCEL_PATTERN)
        files   = sorted(glob.glob(pattern))

        if not files:
            log("No existing data files found - full scrape will run.")
            return existing_ids

        for f in files:
            fname = os.path.basename(f)
            for col in ["ID", "id"]:
                try:
                    df  = pd.read_excel(f, usecols=[col])
                    ids = df[col].dropna().astype(str).tolist()
                    existing_ids.update(ids)
                    log(f"  [FILE] {fname}: loaded {len(ids):,} IDs")
                    break
                except Exception:
                    continue
            else:
                log(f"  [WARN] Could not read ID column from {fname} - skipping dedup")

        log(f"[OK] {len(existing_ids):,} historical IDs loaded.\n")
        return existing_ids

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _warmup(self) -> None:
        """Navigate to reddit homepage to get session cookies."""
        log("[INFO] Warming up session on old.reddit.com...")
        try:
            self.page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            log(f"[INFO] Session ready: {self.page.title()}")
        except Exception as e:
            log(f"[WARN] Warmup failed: {e}")

    def _goto(self, url: str) -> bool:
        """
        Navigate to URL with retry-and-backoff on failure.

        Handles three failure modes:
          1. Network error (ERR_CONNECTION_CLOSED, timeout) - server dropped us
          2. Soft block - page loads but is a CAPTCHA / error / over-18 gate
          3. Login redirect - content requires an account

        Returns True only when a good page is loaded.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            # --- navigate ---
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                log(f"      [WARN] Attempt {attempt}/{MAX_RETRIES} - network error: "
                    f"{type(e).__name__} - backing off {wait}s")
                time.sleep(wait)
                continue   # retry

            # --- jittered delay after every successful load (polite + human-like) ---
            time.sleep(PAGE_DELAY + random.uniform(*JITTER))

            # --- hard redirect to login ---
            current = self.page.url
            if "reddit.com/login" in current or "reddit.com/register" in current:
                log("[ERR] Redirected to login - content requires auth.")
                return False   # no point retrying

            # --- soft-block / error page detection ---
            try:
                title = self.page.title().lower()
            except Exception:
                title = ""
            if any(kw in title for kw in ("blocked", "captcha", "error", "429", "too many")):
                wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                log(f"      [WARN] Attempt {attempt}/{MAX_RETRIES} - soft block detected "
                    f"(title: {title[:40]!r}) - backing off {wait}s")
                time.sleep(wait)
                continue   # retry

            return True   # good page

        log(f"      [ERR] All {MAX_RETRIES} attempts failed for: {url}")
        return False

    def _next_page_url(self) -> str | None:
        """
        Return the pagination 'next' URL.
        Works for both subreddit listings (span.next-button > a)
        and search pages (a[rel*='next'] inside span.nextprev).
        """
        try:
            # a[rel*='next'] covers both page types
            btn = self.page.query_selector("a[rel*='next']")
            if btn:
                href = btn.get_attribute("href")
                # Exclude subreddit-type search pagination (type=sr links)
                if href and "type=sr" not in href:
                    return href
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Record builders
    # ------------------------------------------------------------------

    def _make_post_record(self, post: dict, keyword: str, body: str = "") -> dict:
        return {
            "ID":             post["fullname"],
            "Parent_ID":      "ROOT",
            "Posted_Time":    datetime.datetime.fromtimestamp(
                                  post["created_utc"], tz=timezone.utc
                              ).strftime("%Y-%m-%d %H:%M:%S"),
            "Keyword":        keyword,
            "Data_Type":      "Post",
            "Level":          0,
            "Total_Comments": post["num_comments"],
            "Body":           body,
            "Author":         post["author"],
            "Score":          post["score"],
            "Post_Title":     post["title"],
        }

    def _make_comment_record(self, c: dict, keyword: str,
                             post_title: str, total_comments: int) -> dict:
        parent_id = c.get("parent_fullname", "")
        level = 1 if parent_id.startswith("t3_") else 2
        return {
            "ID":             c["fullname"],
            "Parent_ID":      parent_id,
            "Posted_Time":    datetime.datetime.fromtimestamp(
                                  c["created_utc"], tz=timezone.utc
                              ).strftime("%Y-%m-%d %H:%M:%S"),
            "Keyword":        keyword,
            "Data_Type":      "Comment",
            "Level":          level,
            "Total_Comments": total_comments,
            "Body":           c.get("body", ""),
            "Author":         c.get("author", "[deleted]"),
            "Score":          c.get("score", 0),
            "Post_Title":     post_title,
        }

    # ------------------------------------------------------------------
    # Post page fetch (body + comments)
    # ------------------------------------------------------------------

    def fetch_post_page(self, post: dict, keyword: str,
                        existing_ids: set[str]) -> list[dict]:
        """
        Navigate to a post's permalink, extract self-text + all comments.
        Returns [post_record, ...comment_records] or [] if skipped/failed.
        """
        fullname  = post["fullname"]
        permalink = post.get("permalink", "")

        if fullname in existing_ids or fullname in self.scraped_post_ids:
            log(f"      [SKIP] {post.get('title', '')[:60]}")
            return []

        if not permalink:
            log(f"      [WARN] No permalink for {fullname} - skipping.")
            return []

        # ?limit=500 loads as many comments as old Reddit will serve
        url = f"{BASE_URL}{permalink}?limit=500"
        if not self._goto(url):
            # Save to failed queue so this post can be retried in a later run
            self.failed_posts.append({
                "fullname":     fullname,
                "permalink":    permalink,
                "keyword":      keyword,
                "title":        post.get("title", ""),
                "author":       post.get("author", ""),
                "score":        post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created_utc":  post.get("created_utc", 0),
            })
            log(f"      [QUEUED] Added to failed-post retry file "
                f"({len(self.failed_posts)} total)")
            return []

        # Self-post body (empty for link posts)
        body = ""
        try:
            body = self.page.evaluate(_POST_BODY_JS) or ""
        except Exception:
            pass

        # Expand "load more comments" links before extracting
        try:
            extra = self.page.evaluate(_LOAD_MORE_JS)
            if extra:
                log(f"      [+] Expanded {extra} 'load more' link(s)")
        except Exception:
            pass

        # All comments via JS
        raw_comments: list[dict] = []
        try:
            raw_comments = self.page.evaluate(_COMMENT_EXTRACT_JS) or []
        except Exception as e:
            log(f"      [WARN] Comment extraction error: {e}")

        comment_records = [
            self._make_comment_record(c, keyword, post["title"], post["num_comments"])
            for c in raw_comments
        ]
        log(f"      -> {len(comment_records)} comments fetched")

        return [self._make_post_record(post, keyword, body)] + comment_records

    # ------------------------------------------------------------------
    # Generic paginating listing scraper
    # ------------------------------------------------------------------

    def _scrape_listing(
        self,
        start_url:    str,
        extract_js:   str,
        keyword:      str,
        min_ts:       int,
        max_posts:    int | None,
        existing_ids: set[str],
    ) -> int:
        """
        Paginate through an old Reddit listing (search results or subreddit /new/).
        - extract_js: _LISTING_EXTRACT_JS or _SEARCH_EXTRACT_JS depending on page type
        - Collects posts with created_utc >= min_ts
        - Stops when the oldest post on a page is before min_ts OR max_posts reached
        Returns: count of new posts added to self.all_results
        """
        new_posts = 0
        url       = start_url
        page_num  = 0
        seen_ids: set[str] = set()

        while url:
            page_num += 1
            if not self._goto(url):
                log(f"   [WARN] Page {page_num} load failed - stopping.")
                break

            try:
                posts: list[dict] = self.page.evaluate(extract_js) or []
            except Exception as e:
                log(f"   [ERR] Extraction error on page {page_num}: {e} - stopping.")
                break

            if not posts:
                log(f"   [INFO] Page {page_num} empty - done.")
                break

            oldest_ts = min(p["created_utc"] for p in posts)
            newest_ts = max(p["created_utc"] for p in posts)
            oldest_dt = datetime.datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
            newest_dt = datetime.datetime.fromtimestamp(newest_ts, tz=timezone.utc)
            log(f"   [PAGE] Page {page_num} | {len(posts)} posts | "
                f"{oldest_dt.strftime('%Y-%m-%d')} -> {newest_dt.strftime('%Y-%m-%d')}")

            hit_cap = False
            for post in posts:
                if max_posts is not None and new_posts >= max_posts:
                    hit_cap = True
                    break

                pid = post["id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

                if post["created_utc"] < min_ts:
                    continue   # too old - skip but keep scanning this page

                if post["num_comments"] <= MIN_COMMENTS:
                    log(f"      [LOW] {post['title'][:55]} ({post['num_comments']} comments - skip)")
                    continue   # not enough discussion to be useful

                log(f"   [{new_posts + 1}] {post['title'][:65]} ({post['num_comments']} comments)")
                records = self.fetch_post_page(post, keyword, existing_ids)
                if records:
                    self.all_results.extend(records)
                    self.scraped_post_ids.add(post["fullname"])
                    new_posts += 1

            if hit_cap:
                log(f"   [INFO] Reached cap of {max_posts} posts.")
                break

            # If entire page is older than our cutoff, no point going deeper
            if oldest_ts < min_ts:
                log(f"   [DATE] Oldest post on page is before cutoff - done.")
                break

            next_url = self._next_page_url()
            if not next_url:
                log(f"   [INFO] No next page - done.")
                break
            url = next_url

        return new_posts

    # ------------------------------------------------------------------
    # Phase 1: keyword search
    # ------------------------------------------------------------------

    def scrape_keywords(self, existing_ids: set[str]) -> None:
        run_keywords = [KEYWORDS[0]] if TEST_MODE else KEYWORDS
        log("=" * 65)
        log(f"[PHASE 1] Keyword search via old.reddit.com (live data)")
        log(f"          {len(run_keywords)} keywords | max {POSTS_PER_KEYWORD} posts each")
        log(f"          Date range: {KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} -> now")
        log(f"          Min comments: >{MIN_COMMENTS} (low-engagement posts skipped)")
        log("=" * 65)

        for kw in run_keywords:
            log(f"\n[KW] [{kw}]")
            # type=link: post-only results with full 25/page + after= pagination
            # sort=new: newest first so we stop when we hit the date cutoff
            url = (
                f"{BASE_URL}/search/"
                f"?q={url_quote(kw)}&sort=new&t=all&type=link"
            )
            new_posts = self._scrape_listing(
                url, _SEARCH_EXTRACT_JS, kw,
                KEYWORD_MIN_TS, POSTS_PER_KEYWORD, existing_ids
            )
            log(f"   [DONE] [{kw}] - {new_posts} new posts added.")
            self.save_data(f"kw_{kw[:20]}")

            if kw != run_keywords[-1]:
                log(f"   [WAIT] Pausing {SECTION_PAUSE:.0f}s...")
                time.sleep(SECTION_PAUSE)

    # ------------------------------------------------------------------
    # Phase 2: subreddit full scrape
    # ------------------------------------------------------------------

    def scrape_subreddits(self, existing_ids: set[str]) -> None:
        run_subs = [SUBREDDITS[0]] if TEST_MODE else SUBREDDITS
        log("\n" + "=" * 65)
        log(f"[PHASE 2] Subreddit full scrape via old.reddit.com")
        log(f"          {len(run_subs)} subreddits | posts from "
            f"{SUBREDDIT_MIN_DATE.strftime('%Y-%m-%d')} to now")
        log(f"          Min comments: >{MIN_COMMENTS} (low-engagement posts skipped)")
        log("=" * 65)

        for sub in run_subs:
            log(f"\n[SUB] r/{sub}")
            url = f"{BASE_URL}/r/{sub}/new/"
            new_posts = self._scrape_listing(
                url, _LISTING_EXTRACT_JS, f"r/{sub}",
                SUBREDDIT_MIN_TS, None, existing_ids
            )
            log(f"   [DONE] r/{sub} - {new_posts} new posts added.")
            self.save_data(f"sub_{sub[:20]}")

            if sub != run_subs[-1]:
                log(f"   [WAIT] Pausing {SECTION_PAUSE:.0f}s...")
                time.sleep(SECTION_PAUSE)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_failed_posts(self) -> None:
        """Write failed posts to a JSON file for later retry."""
        if not self.failed_posts:
            log("[INFO] No failed posts - nothing to save.")
            return
        path = os.path.join(self.base_path, FAILED_POSTS_FILE)
        # Merge with any existing file from a previous partial run
        existing: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        merged = {p["fullname"]: p for p in existing + self.failed_posts}  # deduplicate
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(merged.values()), f, indent=2, ensure_ascii=False)
        log(f"[SAVE] {len(merged)} failed post(s) -> {FAILED_POSTS_FILE}")
        log(f"       To retry: set RETRY_FILE = '{FAILED_POSTS_FILE}' and re-run.")

    def retry_failed(self, retry_file: str, existing_ids: set[str]) -> None:
        """
        Re-fetch posts that failed during a previous run.
        Load from a JSON file saved by save_failed_posts().
        """
        path = os.path.join(self.base_path, retry_file)
        if not os.path.exists(path):
            log(f"[ERR] Retry file not found: {retry_file}")
            return
        with open(path, encoding="utf-8") as f:
            pending: list[dict] = json.load(f)
        log(f"[RETRY] {len(pending)} failed posts from {retry_file}")

        recovered = []
        still_failed = []
        for i, p in enumerate(pending, 1):
            log(f"  [{i}/{len(pending)}] {p.get('title','')[:60]}")
            records = self.fetch_post_page(p, p.get("keyword", "retry"), existing_ids)
            if records:
                self.all_results.extend(records)
                self.scraped_post_ids.add(p["fullname"])
                recovered.append(p["fullname"])
            else:
                still_failed.append(p)
            time.sleep(SECTION_PAUSE)

        log(f"[RETRY] Done: {len(recovered)} recovered, {len(still_failed)} still failing.")

        # Overwrite the file with only the ones that still failed
        if still_failed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(still_failed, f, indent=2, ensure_ascii=False)
            log(f"[SAVE] {len(still_failed)} still-failing posts -> {retry_file}")
        else:
            os.remove(path)
            log(f"[INFO] All recovered - removed {retry_file}")

        self.save_data("retry")

    def save_data(self, tag: str) -> None:
        if not self.all_results:
            return
        df = pd.DataFrame(self.all_results).drop_duplicates(subset=["ID"])
        filename = f"popmart_v4.0_{datetime.date.today()}.xlsx"
        path = os.path.join(self.base_path, filename)
        try:
            df.to_excel(path, index=False)
            log(f"[SAVE] {tag} | {len(df):,} rows -> {filename}")
        except Exception as e:
            log(f"[ERR] Save failed: {e}")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        start_time = time.time()
        log("[START] Popmart Reddit scraper v4.0 (Playwright + old.reddit.com)")
        log(f"        Browser: Chromium | headless={HEADLESS}")
        log(f"        Source:  {BASE_URL}")
        log(f"        Range:   {KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} -> now (real-time)\n")

        self._warmup()   # get session cookies before scraping

        existing_ids = self.load_existing_ids()

        if RETRY_FILE:
            # Retry-only mode: re-fetch posts that failed in a previous run
            log(f"[MODE] Retry-only mode - reading from {RETRY_FILE}")
            self.retry_failed(RETRY_FILE, existing_ids)
        else:
            # Normal full scrape
            self.scrape_keywords(existing_ids)
            self.scrape_subreddits(existing_ids)
            self.save_data("FINAL")

        self.save_failed_posts()   # always save whatever failed this run

        elapsed = time.time() - start_time
        h, m = divmod(int(elapsed), 3600)
        m, s = divmod(m, 60)
        unique_new = len(set(r["ID"] for r in self.all_results))
        log(f"\n[DONE] All tasks complete.")
        log(f"       New records : {unique_new:,}")
        log(f"       Failed posts: {len(self.failed_posts)}")
        log(f"       Elapsed     : {h}h {m}m {s}s")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    with sync_playwright() as pw:
        scraper = PopmartScraperPlaywright(pw)
        try:
            scraper.run()
        finally:
            scraper.close()
