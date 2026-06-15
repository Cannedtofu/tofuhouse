from __future__ import annotations

import sys
import requests
import pandas as pd
import time
import datetime
from datetime import timezone
import os
import glob

# ========================= Configuration =========================
USER_AGENT = "popmart_key_words_project:v3.0 (research; contact /u/MasterTofu996)"

# PullPush: free community Reddit archive (Pushshift successor)
# Docs: https://api.pullpush.io  |  Rate limits: 15 req/min soft, 1000 req/hr hard
BASE_URL = "https://api.pullpush.io"

# Date filters
# PullPush archive covers up to ~May 2025, so we pull everything from 2025.
# The existing Jan-2026 keyword data is already in our Excel files; 2025 is all new.
KEYWORD_MIN_DATE   = datetime.datetime(2025, 1, 1, tzinfo=timezone.utc)
SUBREDDIT_MIN_DATE = datetime.datetime(2025, 1, 1, tzinfo=timezone.utc)

KEYWORD_MIN_TS   = int(KEYWORD_MIN_DATE.timestamp())
SUBREDDIT_MIN_TS = int(SUBREDDIT_MIN_DATE.timestamp())

KEYWORDS = [
    "popmart", "labubu", "popmart hirono", "popmart skullpanda",
    "popmart peach riot", "popmart twinkle twinkle", "popmart crybaby",
    "popmart molly", "popmart dimoo"
]

SUBREDDITS = [
    "SkullpandaArtDolls", "labubu", "CryBabyDolls", "hirono",
    "peachriot", "PopMartCollectors", "Dimoos", "TwinkleTwinkleCollect"
]

TEST_MODE         = False
POSTS_PER_KEYWORD = 1 if TEST_MODE else 100   # keyword search cap per keyword

EXISTING_EXCEL_PATTERN = "popmart_v*.xlsx"

# Stay well within PullPush rate limits: 15 req/min soft = 1 per 4s
# 1000 req/hr hard = 1 per 3.6s  ->  use 4s to be safe
REQUEST_DELAY = 4.0    # seconds between API requests
SECTION_PAUSE = 10.0   # seconds between keywords / subreddits
# =================================================================


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


class PopmartScraper:

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.all_results: list[dict] = []
        self.scraped_post_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Helpers
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

    def fetch_json(self, url: str, params: dict | None = None) -> dict | None:
        """GET with rate-limit header awareness and one retry on 429."""
        try:
            resp = self.session.get(url, params=params, timeout=20)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                log(f"[WARN] 429 rate-limited - sleeping {retry_after + 5}s")
                time.sleep(retry_after + 5)
                resp = self.session.get(url, params=params, timeout=20)
                if resp.status_code == 200:
                    return resp.json()

            log(f"[WARN] HTTP {resp.status_code}: {url}")
            return None

        except Exception as e:
            log(f"[ERR] Network error: {e}")
            return None

    # ------------------------------------------------------------------
    # Data conversion
    # ------------------------------------------------------------------

    def _make_post_record(self, post: dict, keyword: str) -> dict:
        # PullPush returns `name` with full t3_ prefix already
        post_id = post.get("name") or f"t3_{post.get('id', '')}"
        return {
            "ID":             post_id,
            "Parent_ID":      "ROOT",
            "Posted_Time":    datetime.datetime.fromtimestamp(
                                  post.get("created_utc", 0), tz=timezone.utc
                              ).strftime("%Y-%m-%d %H:%M:%S"),
            "Keyword":        keyword,
            "Data_Type":      "Post",
            "Level":          0,
            "Total_Comments": post.get("num_comments", 0),
            "Body":           post.get("selftext", ""),
            "Author":         post.get("author", "[deleted]"),
            "Score":          post.get("score", 0),
            "Post_Title":     post.get("title", ""),
        }

    def _make_comment_record(self, comment: dict, keyword: str,
                             post_title: str, total_comments: int) -> dict:
        cid       = comment.get("name") or f"t1_{comment.get('id', '')}"
        parent_id = comment.get("parent_id", "")
        # Compute approximate level from parent_id prefix
        level = 1 if parent_id.startswith("t3_") else 2
        return {
            "ID":             cid,
            "Parent_ID":      parent_id,
            "Posted_Time":    datetime.datetime.fromtimestamp(
                                  comment.get("created_utc", 0), tz=timezone.utc
                              ).strftime("%Y-%m-%d %H:%M:%S"),
            "Keyword":        keyword,
            "Data_Type":      "Comment",
            "Level":          level,
            "Total_Comments": total_comments,
            "Body":           comment.get("body", ""),
            "Author":         comment.get("author", "[deleted]"),
            "Score":          comment.get("score", 0),
            "Post_Title":     post_title,
        }

    def fetch_comments(self, post_id_short: str, post_title: str,
                       keyword: str, total_comments: int) -> list[dict]:
        """
        Fetch all comments for a post via PullPush, paginating if needed.
        post_id_short: the bare Reddit post ID without t3_ prefix.
        """
        records: list[dict] = []
        after_ts = 0
        seen: set[str] = set()

        while True:
            params: dict = {
                "link_id":   post_id_short,
                "sort_type": "created_utc",
                "sort":      "asc",
                "size":      100,
            }
            if after_ts:
                params["after"] = after_ts

            data = self.fetch_json(
                f"{BASE_URL}/reddit/search/comment/", params
            )
            time.sleep(REQUEST_DELAY)

            if not data:
                break

            comments = data.get("data", [])
            if not comments:
                break

            new_on_page = 0
            for c in comments:
                cid = c.get("id", "")
                if cid in seen:
                    continue
                seen.add(cid)
                records.append(self._make_comment_record(
                    c, keyword, post_title, total_comments
                ))
                new_on_page += 1
                after_ts = max(after_ts, c.get("created_utc", 0))

            # Stop if we got fewer than a full page (last page)
            if len(comments) < 100 or new_on_page == 0:
                break

        return records

    def fetch_post_with_comments(
        self,
        post: dict,
        keyword: str,
        existing_ids: set[str],
    ) -> tuple[list, str | None]:
        post_fullname = post.get("name") or f"t3_{post.get('id', '')}"
        post_id_short = post.get("id", "")

        if post_fullname in existing_ids or post_fullname in self.scraped_post_ids:
            log(f"      [SKIP] {post.get('title', '')[:60]}")
            return [], None

        total_comments = post.get("num_comments", 0)
        records = [self._make_post_record(post, keyword)]

        comments = self.fetch_comments(
            post_id_short,
            post.get("title", ""),
            keyword,
            total_comments,
        )
        records.extend(comments)
        log(f"      -> {len(comments)} comments fetched")

        return records, post_fullname

    def save_data(self, tag: str) -> None:
        if not self.all_results:
            return
        df       = pd.DataFrame(self.all_results).drop_duplicates(subset=["ID"])
        filename = f"popmart_v3.0_{datetime.date.today()}.xlsx"
        path     = os.path.join(self.base_path, filename)
        try:
            df.to_excel(path, index=False)
            log(f"[SAVE] {tag} | {len(df):,} rows -> {filename}")
        except Exception as e:
            log(f"[ERR] Save failed: {e}")

    # ------------------------------------------------------------------
    # Phase 1: keyword search
    # ------------------------------------------------------------------

    def scrape_keywords(self, existing_ids: set[str]) -> None:
        run_keywords = [KEYWORDS[0]] if TEST_MODE else KEYWORDS
        log("=" * 65)
        log(f"[PHASE 1] Keyword search via PullPush")
        log(f"          {len(run_keywords)} keywords | max {POSTS_PER_KEYWORD} new posts each")
        log(f"          Date range: {KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} -> archive limit (~May 2025)")
        log("=" * 65)

        for kw in run_keywords:
            log(f"\n[KW] [{kw}]")
            new_posts = 0
            after_ts  = KEYWORD_MIN_TS
            seen: set[str] = set()

            while new_posts < POSTS_PER_KEYWORD:
                params = {
                    "q":         kw,
                    "sort_type": "created_utc",
                    "sort":      "asc",
                    "size":      100,
                    "after":     after_ts,
                }
                data = self.fetch_json(
                    f"{BASE_URL}/reddit/search/submission/", params
                )
                time.sleep(REQUEST_DELAY)

                if not data:
                    break

                posts = data.get("data", [])
                if not posts:
                    log(f"   [INFO] No more results for [{kw}].")
                    break

                last_ts = after_ts
                for post in posts:
                    if new_posts >= POSTS_PER_KEYWORD:
                        break

                    pid = post.get("id", "")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    last_ts = max(last_ts, post.get("created_utc", last_ts))

                    log(f"   [{new_posts + 1}] {post.get('title', '')[:60]}")
                    records, fullname = self.fetch_post_with_comments(
                        post, kw, existing_ids
                    )
                    if fullname:
                        self.all_results.extend(records)
                        self.scraped_post_ids.add(fullname)
                        new_posts += 1

                if last_ts == after_ts or len(posts) < 100:
                    break
                after_ts = last_ts

                if TEST_MODE:
                    break

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
        log(f"[PHASE 2] Subreddit full scrape via PullPush")
        log(f"          {len(run_subs)} subreddits | all posts from {SUBREDDIT_MIN_DATE.strftime('%Y-%m-%d')}")
        log("=" * 65)

        for sub in run_subs:
            log(f"\n[SUB] r/{sub}")
            new_posts = 0
            after_ts  = SUBREDDIT_MIN_TS
            page      = 0
            seen: set[str] = set()

            while True:
                page += 1
                params = {
                    "subreddit": sub,
                    "sort_type": "created_utc",
                    "sort":      "asc",
                    "size":      100,
                    "after":     after_ts,
                }
                data = self.fetch_json(
                    f"{BASE_URL}/reddit/search/submission/", params
                )
                time.sleep(REQUEST_DELAY)

                if not data:
                    log(f"   [WARN] Page {page} request failed - stopping.")
                    break

                posts = data.get("data", [])
                if not posts:
                    log(f"   [INFO] Page {page} empty - done.")
                    break

                oldest = datetime.datetime.fromtimestamp(
                    posts[0].get("created_utc", 0), tz=timezone.utc
                )
                newest = datetime.datetime.fromtimestamp(
                    posts[-1].get("created_utc", 0), tz=timezone.utc
                )
                log(f"   [PAGE] Page {page} | {len(posts)} posts | "
                    f"{oldest.strftime('%Y-%m-%d')} -> {newest.strftime('%Y-%m-%d')}")

                last_ts = after_ts
                for post in posts:
                    pid = post.get("id", "")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    last_ts = max(last_ts, post.get("created_utc", last_ts))

                    records, fullname = self.fetch_post_with_comments(
                        post, f"r/{sub}", existing_ids
                    )
                    if fullname:
                        self.all_results.extend(records)
                        self.scraped_post_ids.add(fullname)
                        new_posts += 1

                if last_ts == after_ts or len(posts) < 100:
                    log(f"   [INFO] Last page reached.")
                    break
                after_ts = last_ts

            log(f"   [DONE] r/{sub} - {new_posts} new posts added.")
            self.save_data(f"sub_{sub[:20]}")

            if sub != run_subs[-1]:
                log(f"   [WAIT] Pausing {SECTION_PAUSE:.0f}s...")
                time.sleep(SECTION_PAUSE)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        log("[START] Popmart Reddit scraper v3.0 (PullPush)")
        log(f"        Date range: {KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} -> ~May 2025 (PullPush archive limit)")
        log(f"        Data source: {BASE_URL}\n")

        existing_ids = self.load_existing_ids()
        self.scrape_keywords(existing_ids)
        self.scrape_subreddits(existing_ids)
        self.save_data("FINAL")

        unique_new = len(set(r["ID"] for r in self.all_results))
        log(f"\n[DONE] All tasks complete. {unique_new:,} unique new records added.")


if __name__ == "__main__":
    PopmartScraper().run()
