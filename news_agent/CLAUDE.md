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
- `QWEN_API_KEY` — Alibaba Cloud DashScope key (from bailian.console.aliyun.com). Used for **all** LLM tasks: article summaries, digest, vision agent, speech-to-text.
- `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAIL`, `SMTP_SERVER`, `SMTP_PORT` — email delivery via `main.py`
- `YOUTUBE_COOKIES_FILE` — path to a Netscape-format cookies file for yt-dlp (only needed when the youtube-transcript-api fast path fails, i.e. videos with no English captions)

Python 3.11+ required (browser-use and playwright require 3.11+). Use `.venv` created with `py -3.12`.

ffmpeg must be installed on the server for the YouTube audio fallback path:
```bash
apt-get install ffmpeg
```

## Architecture

Two entry points share the same pipeline:

- **`app.py`** — Flask web UI. Sources and articles managed in browser. Fetch triggered via AJAX (`POST /fetch`) in a background thread. APScheduler fetches Nitter sources on a configurable schedule.
- **`main.py`** — CLI for GitHub Actions. Full pipeline: fetch → summarize → digest → email.

### Data flow — news feed

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
article_summarizer.py   → Qwen qwen-plus (per-article 2-3 sentence blurbs)
          ↓
email_digest.py         → plain-text markdown builder for email
          ↓
email_sender.py         → SMTP
```

### Data flow — AI digest (web UI)

```
POST /digest/generate  (user clicks "Generate AI Digest")
    ↓
ai_digest.generate_batch_digest()  [daemon thread]
    ├── per RSS/web source: importance classification → abstracts for top articles
    ├── per Nitter source: original-tweet discourse + retweet signal summaries
    └── cross-source Big Picture synthesis (when >1 source)
    ↓
cached in digests table (keyed by sorted article ID hash)
```

### Data flow — YouTube transcript

```
POST /transcript/process  (user pastes YouTube URL + chooses mode)
    ↓
transcript_worker.process_transcript_job()  [daemon thread]
    ↓
Mode: 不区分发言人 (no diarization)
    Step 1: yt-dlp subtitle download (caption file only, no audio)
        → if captions found: plain text transcript → done
        → if no captions: pause at "awaiting_approval" — user must confirm audio download

    Step 2 (after user approval): audio download + paraformer-v2 ASR (no diarization)
        ├── yt-dlp: download full audio (mp3, mono, 64 kbps) to /tmp
        └── DashScope Recognition.call(): full file in one call (≤12 hours supported)

Mode: 区分发言人 (diarization)
    Always starts audio download immediately (user chose this mode knowingly)
        ├── yt-dlp: download full audio to /tmp
        └── DashScope Recognition.call(diarization_enabled=True)
              → sentence_info with speaker_id → formatted as [Speaker A] paragraphs
              (consistent speaker IDs throughout the full video — no chunking)

Step 3 (user-activated): AI summary via transcript_worker.generate_transcript_summary()
    ├── if transcript ≤ 12,000 chars: single Qwen call
    └── if longer: chunk summaries → final synthesis

Step 4: store results, set status "done"
    ↓
GET /transcript/status/<job_id>  (frontend polls every 3s)
GET /transcript/download/<job_id>  (returns .txt with summary + full transcript)
```

---

## Key design decisions

### Playwright in headed (non-headless) mode
Cloudflare and other bot-detection systems fingerprint headless Chrome via empty plugin list, SwiftShader WebGL, and mismatched screen dimensions. Running headed but off-screen (`--window-position=-32000,-32000`) passes all fingerprint checks. Browser window is invisible to the user.

### Two-tier content extraction
`browser_use_fetcher.py` runs Playwright first (fast, no LLM). If fewer than `MIN_BROWSER_FALLBACK_CHARS` (300) characters are extracted, the browser-use LLM agent takes over — it can interact with the page (scroll, dismiss banners) before extracting. Qwen `qwen3-vl-flash` is used for the vision agent.

### `fetch_article` vs `fetch_article_with_meta`
`browser_use_fetcher.py` exposes two public functions:
- `fetch_article(url) -> str` — content only; used by the RSS enrichment path.
- `fetch_article_with_meta(url) -> (str, str | None)` — content + publication date extracted from page metadata via `trafilatura.extract_metadata(html)`; used only by the web fetcher. The raw HTML is already in memory from Playwright so the date costs nothing extra.

### Web source date handling (two-stage)
`fetch_web()` applies dates in two stages to handle JS-heavy sites (e.g. a16z.com) where the listing page may not show dates:
1. **Pre-filter on listing-page date** (from LLM agent) — if present and clearly out of range, skip without a browser call.
2. **Authoritative check on article-page date** (from trafilatura metadata) — extracted during the Playwright fetch. This date is stored as `published_at` and used for the final date gate. Corrects cases where the LLM returned wrong or null dates.
Articles with no date from either source are kept (conservative — they may be newly published).

### Image extraction with lazy-load handling
After page load, Playwright scrolls to the bottom and back to trigger lazy-loaded images. `_extract_with_images()` in `browser_use_fetcher.py` runs trafilatura HTML output through a custom `_soup_to_markdown()` converter that handles `<figure>/<picture>/<img>` nesting and Substack's `data-attrs` JSON (which contains clean, permanent S3 URLs without expiring CDN tokens).

### RSS image detection
`_entry_to_article_basic()` in `rss.py` checks if the RSS body contains `<img` tags. If so, `needs_full_content=True` regardless of body length — the full article is always fetched via Playwright to get images in context. Text-only feeds only fetch via Playwright when the RSS body is shorter than `CONTENT_LENGTH_THRESHOLD`.

### Source `url_filter`
Sources have an optional `url_filter` string. After fetching, `pipeline.py` drops articles whose URL doesn't contain that substring. Used to restrict OpenAI's feed to `openai.com/index/` articles only.

### Source auto-detection
`fetchers/detect.py` identifies source type from a raw URL: X.com/Twitter → nitter; direct feed URL → rss; RSS autodiscovery `<link>` tag → rss; common feed path probing → rss; fallback → web. The Sources UI shows a Detect button that previews the result before saving.

### Admin-only source deletion
The delete route (`POST /sources/<id>/delete`) is protected — returns 403 unless the logged-in user's email matches `ADMIN_EMAIL` (config). The delete button in `sources.html` is rendered only for the admin account via a Jinja2 conditional. All other users see no delete UI at all.

### Thin-content re-fetch
Articles already in the DB are added to `known_urls` (skip list) only if their stored content is ≥ `MIN_CONTENT_WORDS` (200 words). Thin articles are re-fetched so Playwright can fill in full content.

### Nitter periodic background fetch
APScheduler (daemon thread) fetches all Nitter sources on a clock-anchored schedule (default: daily at 11pm SGT). Since Nitter RSS only returns ~20 recent posts, running periodically builds up a historical database over time. The scheduler status is shown as a badge in the feed UI.

### YouTube transcript fast path
`_fetch_transcript_fast()` in `transcript_worker.py` downloads subtitles via yt-dlp (caption file only, no audio). Pass 1 tries English + Chinese; Pass 2 tries all languages. Returns `None` if no captions found, triggering the audio fallback approval flow.

### YouTube audio transcription — no chunking
paraformer-v2 supports up to 2 GB / 12 hours per call (diarization recommended ≤2 hours). The full audio file is sent in a single `Recognition.call()` — no chunking. This is critical for diarization mode: chunking would reset speaker IDs at each chunk boundary, making Speaker A in chunk 1 potentially different from Speaker A in chunk 2.

### AI Digest (batch, structured)
`ai_digest.generate_batch_digest()` groups articles by source. Nitter sources get separate Qwen calls for original tweets vs retweets. RSS/web sources get per-article abstracts. Sections joined with `---`, rendered as HTML via `renderDigest()` in `index.html`.

### Background job patterns
All long-running work uses `threading.Thread(target=_run, daemon=True).start()`:
- News fetch: protected by `_fetch_lock`; status tracked in `_fetch_status` dict
- AI digest: jobs tracked in `_digest_jobs` dict (in-memory, keyed by UUID)
- YouTube transcript: jobs persisted in `transcript_jobs` SQLite table (survives restarts)

---

## Database

SQLite (`news.db`) with WAL mode. `check_same_thread=False, timeout=30` on every connection to handle concurrent background threads without locking errors.

Schema created/migrated by `db.init_db()`. New columns added via try/except `ALTER TABLE` blocks (idempotent migrations).

### Tables

**`sources`** — `id, name, type (rss|nitter|web), url, url_filter, last_fetched`

**`articles`** — `id, source_id, title, url (UNIQUE), content, published_at, fetched_at, summary, digest_abstract`

**`transcript_jobs`** — `job_id (UUID PK), video_url, video_id, video_title, video_author, mode (no_diarization|diarization), status (pending|processing|awaiting_approval|summarizing|done|error), transcript, summary, error_message, created_at, updated_at`
- Persisted to SQLite so jobs survive app restarts
- Polled by the frontend every 3 seconds while status is pending/processing
- Cached by (video_id, mode): re-submitting same URL+mode returns existing done job instantly

**`fetch_log`** — `id, started_at, finished_at, trigger, total_new, total_fetched, sources_json, error`

**`users`** — `id, email (UNIQUE), created_at, last_seen, digest_enabled, digest_frequency_days, digest_last_sent`

**`user_source_follows`** — `(user_id, source_id)` — which sources each user follows

**`digests`** — `id, article_ids_hash, article_ids_json, content, created_at` — full digest cache keyed by sorted article ID hash

**`token_usage`** — `id, user_id, operation, model, tokens_in, tokens_out, created_at`

---

## Routes

### News feed
| Method | Path | Description |
|---|---|---|
| GET | `/` | Main feed (auth required) |
| POST | `/fetch` | Trigger background fetch (AJAX) |
| GET | `/fetch/status` | Poll fetch progress |
| GET | `/articles/<id>` | Article JSON |
| POST | `/articles/<id>/summarize` | On-demand article summary |
| POST | `/digest/generate` | Start AI digest job |
| GET | `/digest/status/<job_id>` | Poll digest job |
| GET | `/digest` | Plain-text digest export |

### Sources
| Method | Path | Description |
|---|---|---|
| GET/POST | `/sources` | List + add sources (auth required) |
| POST | `/sources/<id>/follow` | Follow/unfollow |
| POST | `/sources/<id>/delete` | Delete source (**admin only**) |
| POST | `/sources/detect` | Auto-detect source type from URL |

### YouTube transcript
| Method | Path | Description |
|---|---|---|
| GET | `/transcript` | Transcript UI tab |
| POST | `/transcript/process` | Validate URL, create job, start worker |
| GET | `/transcript/status/<job_id>` | Poll job (pending/processing/done/error) |
| GET | `/transcript/download/<job_id>` | Download summary + transcript as .txt |

### Other
| Method | Path | Description |
|---|---|---|
| GET/POST | `/identify` | Email-only login |
| POST | `/logout` | Clear session |
| GET | `/scheduler/status` | APScheduler next-run info |
| GET | `/logs` | Fetch log + raw app.log tail |
| GET/POST | `/settings` | User digest settings + token usage |

---

## Config constants (`config.py`)

| Constant | Purpose |
|---|---|
| `QWEN_API_KEY` / `QWEN_BASE_URL` | Alibaba DashScope credentials — used for all LLM calls |
| `QWEN_SUMMARY_MODEL` | `qwen-plus` — article summaries, digest, transcript summarization |
| `QWEN_VISION_MODEL` | `qwen3-vl-flash` — browser-use vision agent fallback only |
| `MIN_BROWSER_FALLBACK_CHARS` | Playwright result shorter than this triggers agent (300) |
| `DATE_RANGE_DAYS` | Default fetch lookback window (7 days) |
| `MIN_ARTICLE_DATE` | Hard floor — articles before this date always dropped |
| `MAX_ARTICLES_PER_SOURCE` | Cap per source per fetch run (50) |
| `CONTENT_LENGTH_THRESHOLD` | RSS body shorter than this triggers Playwright fetch (500) |
| `MIN_CONTENT_WORDS` | Existing DB articles shorter than this are re-fetched (200) |
| `NITTER_FETCH_PERIOD_HOURS` | Scheduler interval AND tweet pagination cutoff (24h default) |
| `NITTER_PAGE_DELAY` | Seconds between Nitter HTML page requests (120s default) |
| `NITTER_INSTANCES` | Nitter instance URLs tried in order |
| `DEFAULT_SOURCES` | Seeded into DB on first startup |
| `SECRET_KEY` | Flask session signing key |
| `EMAIL_WHITELIST` | Comma-separated emails allowed to sign in (empty = open) |
| `ADMIN_EMAIL` | Single admin account with source deletion rights |
| `YOUTUBE_COOKIES_FILE` | Path to Netscape cookies file for yt-dlp (audio fallback only) |
| `SMTP_*` | Email delivery settings |

---

## File structure

```
app.py                          Flask app, all routes, APScheduler setup
main.py                         CLI entry point (GitHub Actions)
pipeline.py                     Shared fetch+summarize pipeline
config.py                       All constants and env var loading
article_summarizer.py           Per-article 2-3 sentence blurbs via Qwen
ai_digest.py                    AI-powered web UI digest (classification, abstracts, synthesis)
email_digest.py                 Plain-text markdown builder for email delivery (CLI path)
email_sender.py                 SMTP email delivery
transcript_worker.py            YouTube transcript pipeline (captions → audio fallback → ASR)

db/                             SQLite layer — split by domain
  __init__.py                   Re-exports all public functions (import db; db.fn() still works)
  core.py                       Connection management, schema init, migrations
  sources.py                    News source CRUD
  articles.py                   Article CRUD, digest abstracts
  fetch_log.py                  Fetch run history
  users.py                      User accounts, source follows, digest preferences
  digests.py                    AI digest cache, token usage tracking
  transcripts.py                YouTube transcript jobs

fetchers/
  rss.py                        RSS/Atom + Nitter RSS fetcher
  web.py                        Web source fetcher (LLM link discovery + Playwright content)
  browser_use_fetcher.py        Two-tier content extractor (Playwright + browser-use agent)
                                  fetch_article(url) → str
                                  fetch_article_with_meta(url) → (str, date | None)
  nitter_html.py                Nitter HTML pagination scraper
  detect.py                     Source type auto-detection

templates/
  base.html                     Navbar + Bootstrap 5 shell (nav: 资讯 来源 转录 日志 设置)
  index.html                    Main feed — two-panel layout with AI digest
  sources.html                  Source management — admin sees delete button
  transcript.html               YouTube transcript tab
  logs.html                     Fetch log + raw app.log viewer
  settings.html                 User digest preferences + token usage stats
  identify.html                 Email login

scripts/
  deploy.sh                     Server deploy script
  reset_source.py               Dev utility — clear articles for one source
  fetch_history.py              Admin — backfill historical Nitter tweets
  backup_db.sh                  Weekly DB backup (run by cron, keeps 2 most recent)
```

---

## GitHub Actions

`.github/workflows/daily_report.yml` runs `main.py` daily at 07:00 UTC. `news.db` is persisted between runs as an artifact (30-day retention). All secrets map directly to `.env` variable names.

## Deployment

**Server:** 47.239.66.248 (Alibaba Cloud ECS, Hong Kong)
**App path:** `/opt/tofuhouse/news_agent`
**Service:** `news-agent` (systemd + gunicorn)

Standard deploy:
```bash
git push                        # from local
bash /opt/tofuhouse/news_agent/scripts/deploy.sh   # on server
```

See `DEPLOY.md` for full deployment reference.
