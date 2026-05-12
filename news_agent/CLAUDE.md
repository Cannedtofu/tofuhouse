# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Web UI (development)
python app.py          # serves on http://localhost:5000

# CLI (fetch + summarize + email digest)
python main.py
```

## Environment setup

Copy `.env.example` to `.env` and fill in:
- `GEMINI_API_KEY` — Google Gemini API key
- `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAIL`, `SMTP_SERVER`, `SMTP_PORT` — for email delivery via `main.py`

Python 3.9+ required (codebase uses `from __future__ import annotations` to support `X | Y` union types on 3.9).

## Architecture

Two entry points share the same pipeline:

- **`app.py`** — Flask web UI. Sources and articles are managed through the browser. Fetch is triggered via AJAX (`POST /fetch`) and runs in a background thread.
- **`main.py`** — CLI for GitHub Actions. Runs the full pipeline (fetch → summarize → build digest → send email) in one shot.

### Data flow

```
sources (DB)
    ↓
pipeline.run_fetch_and_summarize()
    ├── fetchers/rss.py     → RSS/Atom feeds + Nitter RSS (X.com)
    ├── fetchers/web.py     → press release index page scraping
    └── db.insert_article() → deduplication by URL, content update if richer
        ↓
summarizer.py               → Gemini API (per-article or batch digest)
        ↓
digest.py                   → markdown builder
        ↓
email_sender.py             → SMTP
```

### Key design decisions

**Selenium for Cloudflare-protected RSS sources (e.g. OpenAI)**
`fetchers/rss.py` uses `undetected_chromedriver` to fetch full article content when the RSS body is shorter than `CONTENT_LENGTH_THRESHOLD` (500 chars). It runs in **headed mode** (not `--headless`) because Cloudflare detects headless Chrome via WebGL/plugin fingerprints. The window is positioned off-screen at `-32000,-32000`. After each `driver.get()`, `_wait_past_cloudflare()` polls `page_source` every 2 s until Cloudflare markers disappear (up to 20 s).

**Thin-content re-fetch**
Articles already in the DB are only added to `known_urls` (skip list) if their stored content is ≥ `MIN_CONTENT_WORDS` (200 words). Articles with thin content are re-fetched so Selenium can fill them in. `db.insert_article()` updates `content` when the new version is longer.

**Source `url_filter`**
Sources have an optional `url_filter` string. After fetching, `pipeline.py` drops any article whose URL doesn't contain that substring. Used to restrict OpenAI's feed to `openai.com/index/` articles only.

**AI Digest (batch, structured)**
`summarizer.generate_batch_digest()` groups visible articles by source. Nitter sources get separate Gemini calls for original tweets vs retweets (retweets identified by title prefix `"RT by @"`). RSS/web sources get a single call producing per-article abstracts. Sections are joined with `---` and rendered as HTML in the browser via `renderDigest()` in `index.html`.

**Default sources**
`config.DEFAULT_SOURCES` is seeded into the DB on first startup (when no sources exist) via `db.seed_default_sources()`, called in `app.py`. Adding sources to `DEFAULT_SOURCES` will seed them on first run only.

### Database

SQLite (`news.db`) with WAL mode. Two tables:
- `sources` — `id, name, type (rss|nitter|web), url, url_filter, last_fetched`
- `articles` — `id, source_id, title, url (UNIQUE), content, published_at, fetched_at, summary`

Schema is created by `db.init_db()`. The `url_filter` column migration is applied automatically via a try/except `ALTER TABLE` in `init_db()` for databases that predate that column.

### Config constants (`config.py`)

| Constant | Purpose |
|---|---|
| `DATE_RANGE_DAYS` | Default fetch lookback window (7 days); overridden by UI date picker |
| `MIN_ARTICLE_DATE` | Hard floor — articles before this date are always dropped |
| `MAX_ARTICLES_PER_SOURCE` | Cap per source per fetch run |
| `CONTENT_LENGTH_THRESHOLD` | RSS body shorter than this triggers Selenium full-fetch |
| `MIN_CONTENT_WORDS` | Existing DB articles shorter than this are re-fetched |
| `NITTER_INSTANCES` | Tried in order; first successful response wins |
| `DEFAULT_SOURCES` | Seeded on first startup |

## GitHub Actions

`.github/workflows/daily_report.yml` runs `main.py` daily at 07:00 UTC. `news.db` is persisted between runs as an artifact (30-day retention). All secrets map directly to `.env` variable names.
