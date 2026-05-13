# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Web UI (development) — requires Python 3.11+ (.venv)
.venv\Scripts\python.exe app.py      # serves on http://localhost:5000

# CLI (fetch + summarize + email digest)
.venv\Scripts\python.exe main.py

# Reset a source's articles for re-testing
.venv\Scripts\python.exe scripts\reset_source.py "semi analysis"
```

## Environment setup

Copy `.env.example` to `.env` and fill in:
- `QWEN_API_KEY` — Alibaba Cloud DashScope key (from bailian.console.aliyun.com). Used for **all** LLM tasks.
- `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAIL`, `SMTP_SERVER`, `SMTP_PORT` — email delivery via `main.py`

Python 3.11+ required (browser-use and playwright require 3.11+). Use `.venv` created with `py -3.12`.

## Architecture

Two entry points share the same pipeline:

- **`app.py`** — Flask web UI. Sources and articles managed in browser. Fetch triggered via AJAX (`POST /fetch`) in a background thread. APScheduler fetches Nitter sources every hour automatically.
- **`main.py`** — CLI for GitHub Actions. Full pipeline: fetch → summarize → digest → email.

### Data flow

```
sources (DB)
    ↓
pipeline.run_fetch_and_summarize()
    ├── fetchers/rss.py       → RSS/Atom feeds + Nitter RSS (X.com)
    ├── fetchers/web.py       → JS-rendered press release page scraping
    └── db.insert_article()   → dedup by URL; update content if new version is richer
          ↓
    fetchers/browser_use_fetcher.py
          ├── Tier 1: Playwright (headed, non-headless) → trafilatura → Markdown + images
          └── Tier 2: browser-use LLM agent (Qwen qwen-vl-max) — only if Tier 1 < 300 chars
          ↓
summarizer.py     → Qwen qwen-plus (article summaries + batch digest)
          ↓
digest.py         → markdown builder
          ↓
email_sender.py   → SMTP
```

### Key design decisions

**Playwright in headed (non-headless) mode**
Cloudflare and other bot-detection systems fingerprint headless Chrome via empty plugin list, SwiftShader WebGL, and mismatched screen dimensions. Running headed but off-screen (`--window-position=-32000,-32000`) passes all fingerprint checks. Browser window is invisible to the user.

**Two-tier content extraction**
`browser_use_fetcher.py` runs Playwright first (fast, no LLM). If fewer than `MIN_BROWSER_FALLBACK_CHARS` (300) characters are extracted, the browser-use LLM agent takes over — it can interact with the page (scroll, dismiss banners) before extracting. Qwen `qwen-vl-max` is used for the vision agent.

**Image extraction with lazy-load handling**
After page load, Playwright scrolls to the bottom and back to trigger lazy-loaded images. `_extract_with_images()` in `browser_use_fetcher.py` runs trafilatura HTML output through a custom `_soup_to_markdown()` converter that handles `<figure>/<picture>/<img>` nesting and Substack's `data-attrs` JSON (which contains clean, permanent S3 URLs without expiring CDN tokens).

**RSS image detection**
`_entry_to_article_basic()` in `rss.py` checks if the RSS body contains `<img` tags. If so, `needs_full_content=True` regardless of body length — the full article is always fetched via Playwright to get images in context. Text-only feeds only fetch via Playwright when the RSS body is shorter than `CONTENT_LENGTH_THRESHOLD`.

**Source `url_filter`**
Sources have an optional `url_filter` string. After fetching, `pipeline.py` drops articles whose URL doesn't contain that substring. Used to restrict OpenAI's feed to `openai.com/index/` articles only.

**Source auto-detection**
`fetchers/detect.py` identifies source type from a raw URL: X.com/Twitter → nitter; direct feed URL → rss; RSS autodiscovery `<link>` tag → rss; common feed path probing → rss; fallback → web. The Sources UI shows a Detect button that previews the result before saving.

**Thin-content re-fetch**
Articles already in the DB are added to `known_urls` (skip list) only if their stored content is ≥ `MIN_CONTENT_WORDS` (200 words). Thin articles are re-fetched so Playwright can fill in full content.

**Nitter periodic background fetch**
APScheduler (daemon thread) fetches all Nitter sources every hour while `app.py` is running. Since Nitter RSS only returns ~20 recent posts, running hourly builds up a historical database over time. The scheduler status is shown as a badge in the feed UI.

**AI Digest (batch, structured)**
`summarizer.generate_batch_digest()` groups articles by source. Nitter sources get separate Qwen calls for original tweets vs retweets. RSS/web sources get per-article abstracts. Sections joined with `---`, rendered as HTML via `renderDigest()` in `index.html`.

**Default sources**
`config.DEFAULT_SOURCES` is seeded into the DB on first startup (when no sources exist) via `db.seed_default_sources()`. Adding sources to `DEFAULT_SOURCES` seeds on first run only.

### Database

SQLite (`news.db`) with WAL mode. Two tables:
- `sources` — `id, name, type (rss|nitter|web), url, url_filter, last_fetched`
- `articles` — `id, source_id, title, url (UNIQUE), content, published_at, fetched_at, summary`

Schema created by `db.init_db()`. The `url_filter` column is auto-migrated via try/except `ALTER TABLE` for older databases.

### Config constants (`config.py`)

| Constant | Purpose |
|---|---|
| `QWEN_API_KEY` / `QWEN_BASE_URL` | Alibaba DashScope credentials |
| `QWEN_SUMMARY_MODEL` | `qwen-plus` — used for article summaries and digest |
| `QWEN_VISION_MODEL` | `qwen-vl-max` — used only for browser-use agent fallback |
| `MIN_BROWSER_FALLBACK_CHARS` | Playwright result shorter than this triggers agent (300) |
| `DATE_RANGE_DAYS` | Default fetch lookback window (7 days) |
| `MIN_ARTICLE_DATE` | Hard floor — articles before this date always dropped |
| `MAX_ARTICLES_PER_SOURCE` | Cap per source per fetch run |
| `CONTENT_LENGTH_THRESHOLD` | RSS body shorter than this triggers Playwright fetch |
| `MIN_CONTENT_WORDS` | Existing DB articles shorter than this are re-fetched |
| `NITTER_INSTANCES` | Tried in order; first successful response wins |
| `DEFAULT_SOURCES` | Seeded on first startup |

## GitHub Actions

`.github/workflows/daily_report.yml` runs `main.py` daily at 07:00 UTC. `news.db` is persisted between runs as an artifact (30-day retention). All secrets map directly to `.env` variable names.
