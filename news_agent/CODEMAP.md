# CODEMAP — news_agent

**Purpose:** Cross-reference map for Claude Code. Read this before editing any file. Update this after any change that affects public function signatures, shared state, or cross-module dependencies.

---

## 1. Module Registry

Quick reference: what each file owns, its public API surface, and what it imports from the project.

### `config.py`
Owns all constants and env vars. No project imports.
**Key constants:**
- `QWEN_SUMMARY_MODEL`, `QWEN_VISION_MODEL`, `ASR_MODEL`, `QWEN_TRANSLATION_MODEL`
- `QWEN_API_KEY`, `QWEN_BASE_URL`
- `DB_PATH`, `AUDIO_CACHE_DIR`, `APP_BASE_URL`, `YOUTUBE_COOKIES_FILE`, `SOCKS_PROXY`
- `MIN_BROWSER_FALLBACK_CHARS` (300), `CONTENT_LENGTH_THRESHOLD` (500), `MIN_CONTENT_WORDS` (200)
- `DATE_RANGE_DAYS` (7), `MAX_ARTICLES_PER_SOURCE` (50), `MIN_ARTICLE_DATE`
- `NITTER_FETCH_PERIOD_HOURS` (24), `NITTER_PAGE_DELAY` (120), `NITTER_INTER_SOURCE_DELAY` (60)
- `NITTER_INSTANCES`, `NITTER_LOCAL_URL`
- `SECRET_KEY`, `EMAIL_WHITELIST`, `ADMIN_EMAIL`
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `RECIPIENT_EMAIL`
- `DEFAULT_SOURCES`

---

### `db/core.py`
Owns connection management and schema creation/migrations.
**Imports:** `config.DB_PATH`
**Public API:**
- `get_conn()` → context manager → `sqlite3.Connection` (WAL, timeout=30s)
- `init_db()` → creates all tables + runs ALTER TABLE migrations

**Tables owned (schema source of truth):**
`sources`, `articles`, `fetch_log`, `users`, `user_source_follows`, `digests`, `token_usage`, `transcript_jobs`, `digest_presets`

---

### `db/sources.py`
Owns source CRUD.
**Imports:** `db.core`
**Public API:**
- `seed_default_sources()` → inserts `DEFAULT_SOURCES` if table empty
- `get_all_sources()` → `list[Row]`
- `get_source_by_id(source_id)` → `Row | None`
- `upsert_source(name, type_, url, url_filter=None)` → `int` (id)
- `delete_source(source_id)` → `None`
- `update_source_last_fetched(source_id)` → `None`

---

### `db/articles.py`
Owns article CRUD and per-article field updates.
**Imports:** `db.core`
**Public API:**
- `insert_article(source_id, title, url, content, published_at)` → `bool` (True=new)
- `get_article_by_id(article_id)` → `Row | None` (JOINs source name + type)
- `get_unsummarized_articles()` → `list[Row]`
- `update_summary(article_id, summary)` → `None`
- `get_articles(date_from=None, date_to=None, source_ids=None)` → `list[Row]`
- `delete_article(article_id)` → `None`
- `get_digest_abstract(article_id)` → `str | None`
- `update_digest_abstract(article_id, abstract)` → `None`
- `update_article_translation(article_id, translated_content)` → `None`

---

### `db/users.py`
Owns user accounts, follows, and digest preferences.
**Imports:** `db.core`
**Public API:**
- `get_or_create_user(email)` → `Row`
- `get_user_by_id(user_id)` → `Row | None`
- `get_all_users()` → `list[Row]`
- `get_users_due_for_digest()` → `list[Row]`
- `get_followed_source_ids(user_id)` → `list[int]`
- `follow_source(user_id, source_id)` → `None`
- `unfollow_source(user_id, source_id)` → `None`
- `get_all_sources_with_follow_status(user_id)` → `list[Row]` (includes `followed` bool)
- `set_user_follows(user_id, source_ids)` → `None` (replaces entire follow list)
- `update_user_digest_settings(user_id, enabled, frequency_days)` → `None`
- `update_user_digest_last_sent(user_id)` → `None`

---

### `db/digests.py`
Owns AI digest cache, token usage logging, and digest presets.
**Imports:** `db.core`
**Key constant:** `MAX_PRESETS_PER_USER = 2`
**Public API:**
- `get_all_digests_with_meta(limit=100)` → `list[dict]`
- `get_digest_cache(article_ids_hash)` → `str | None`
- `save_digest_cache(article_ids_hash, article_ids_json, content)` → `None`
- `log_token_usage(operation, model, tokens_in, tokens_out, user_id=None)` → `None`
- `get_token_usage_summary(user_id=None)` → `list[Row]`
- `get_token_usage_by_user_week()` → `list[Row]`
- `get_digest_presets(user_id)` → `list[dict]`
- `get_digest_presets_for_users(user_ids)` → `list[dict]`
- `get_digest_preset(preset_id, user_id)` → `dict | None`
- `create_digest_preset(user_id, name, source_ids)` → `dict | None`
- `update_digest_preset(preset_id, user_id, name, source_ids, digest_enabled=0, digest_frequency_days=7)` → `None`
- `delete_digest_preset(preset_id, user_id)` → `None`
- `get_presets_due_for_email()` → `list[dict]` (includes `user_email`)
- `update_preset_last_sent(preset_id)` → `None`

---

### `db/transcripts.py`
Owns YouTube transcript job persistence.
**Imports:** `db.core`, `uuid`
**Valid statuses:** `pending`, `processing`, `awaiting_approval`, `summarizing`, `translating`, `done`, `error`
**Public API:**
- `create_transcript_job(video_url, video_id, mode="no_diarization")` → `str` (job_id UUID)
- `get_done_transcript_job(video_id, mode)` → `Row | None`
- `list_transcript_jobs(limit=60)` → `list[Row]`
- `update_transcript_job(job_id, status, transcript=None, transcript_zh=None, summary=None, error_message=None, audio_path=None)` → `None`
- `get_transcript_job(job_id)` → `Row | None`
- `set_transcript_metadata(job_id, video_title, video_author)` → `None`
- `delete_transcript_job(job_id)` → `None`
- `clear_transcript_summary(job_id)` → `None`

---

### `db/fetch_log.py`
Owns fetch run history.
**Imports:** `db.core`
**Public API:**
- `log_fetch_start(trigger="manual")` → `int` (log_id)
- `log_fetch_finish(log_id, result, error=None)` → `None`
- `close_open_fetch_logs()` → `None`
- `get_fetch_log(limit=50)` → `list[Row]`

---

### `db/__init__.py`
Re-exports everything above. **All project code imports `db` and calls `db.fn()`.** Adding a new public function to any `db/` submodule requires adding it here too.

---

### `pipeline.py`
Owns the fetch + summarize pipeline shared between `app.py` and `main.py`.
**Imports:** `db`, `config`, `fetchers.rss`, `fetchers.web`, `article_summarizer`
**Public API:**
- `run_fetch_and_summarize(summarize=False, date_from=None, date_to=None, source_ids=None, source_types=None, exclude_types=None)` → `dict`
  - Returns `{"total_new": int, "sources": [{"name", "type", "new", "fetched", "error"}]}`
  - Routes by source type: `rss` → `fetch_rss()`, `youtube` → `fetch_youtube()`, `nitter` → `fetch_nitter_hybrid()`, `web` → `fetch_web()`
  - Calls `summarize_new_articles()` when `summarize=True`

---

### `article_summarizer.py`
Owns per-article 2-3 sentence summaries.
**Imports:** `db`, `config`
**Note:** `_chat()` and `_get_client()` are private but imported by `ai_digest.py` — treat as semi-public.
**Public API:**
- `summarize_new_articles()` → `int` (count processed); batches in `_BATCH_SIZE=10`
- `summarize_single_article(article_id)` → `str` (returns cached if exists)
**Semi-public (used by ai_digest.py):**
- `_chat(messages, model=None, max_tokens=None)` → `str`
- `_get_client()` → OpenAI client (Qwen-compatible)
- `_SYSTEM_MESSAGE` → str (shared system prompt)

---

### `ai_digest.py`
Owns batch AI digest generation (web UI "Generate AI Digest" feature).
**Imports:** `db`, `article_summarizer._chat/_get_client/_SYSTEM_MESSAGE`, `config`
**Public API:**
- `generate_batch_digest(article_ids, user_id=None)` → `str` (markdown)
  - Caches by sorted article_ids hash
  - Groups by source; Nitter gets tweet/retweet treatment; RSS/web gets briefing + abstracts
  - Runs Big Picture synthesis when >1 source
  - Logs all token usage to DB

---

### `email_digest.py`
Owns plain-text digest for CLI/email path.
**Imports:** `db`
**Public API:**
- `build_email_digest(date_from=None, date_to=None, source_ids=None)` → `str` (markdown)

---

### `email_sender.py`
Owns SMTP delivery.
**Imports:** `config`
**Public API:**
- `send_digest(markdown_body, to_email=None, date_label=None)` → `bool`
  - Converts markdown → HTML, sends multipart SMTP
  - Supports SSL (port 465) and STARTTLS

---

### `transcript_worker.py`
Owns the full YouTube transcript pipeline.
**Imports:** `db`, `config`, `audio_registry`
**Entry points (called by `app.py` daemon threads):**
- `process_transcript_job(job_id, video_url, video_id, mode)` → `None`
- `continue_audio_transcript(job_id, video_id)` → `None` (after user approval)
- `retry_audio_transcript(job_id)` → `None`
- `translate_transcript(job_id)` → `None` (sets `transcript_zh`)
- `generate_transcript_summary(job_id)` → `None`
**Public helpers:**
- `extract_video_id(url)` → `str | None`
- `is_youtube_url(url)` → `bool`

---

### `fetchers/rss.py`
Owns RSS/Atom, YouTube feed, and Nitter RSS/HTML fetching.
**Imports:** `config`, `fetchers.browser_use_fetcher.enrich_with_playwright`
**Public API:**
- `fetch_rss(source_url, known_urls=None, date_from=None)` → `list[dict]`
- `fetch_youtube(feed_url, known_urls=None, date_from=None)` → `list[dict]`
- `fetch_nitter_hybrid(handle, known_urls=None)` → `list[dict]`

---

### `fetchers/web.py`
Owns web index scraping via LLM agent link discovery + Playwright content extraction.
**Imports:** `config`, `browser_use`, `fetchers.browser_use_fetcher`
**Public API:**
- `fetch_web(index_url, known_urls, date_from=None, date_to=None)` → `list[dict]`

---

### `fetchers/browser_use_fetcher.py`
Owns two-tier article content extraction (Playwright → browser-use agent fallback).
**Imports:** `config`, `trafilatura`, `BeautifulSoup`, `browser_use`, `playwright`, `langchain_openai`
**Public API:**
- `fetch_article_with_meta(url, known_urls=set())` → `dict | None` — `{"title", "url", "content", "published_at"}`
- `enrich_with_playwright(articles)` → `list[dict]` — enriches `needs_full_content=True` articles

---

### `fetchers/nitter_html.py`
Owns Nitter HTML pagination.
**Imports:** `config`
**Public API:**
- `fetch_nitter_html_page(handle, cursor=None, instance=None)` → `(list[dict], str | None)`

---

### `fetchers/detect.py`
Owns source type auto-detection from a raw URL.
**Imports:** (no project imports)
**Public API:**
- `detect_source(raw_url)` → `dict` — `{"type", "url", "display", "ok", "error"}`

---

### `app.py`
Flask app. Owns all routes, APScheduler setup, and in-memory job state.
**Imports:** `db`, `config`, `pipeline`, `ai_digest`, `article_summarizer`, `email_digest`, `email_sender`, `transcript_worker`, `fetchers.detect`

**In-memory shared state (module-level):**
```
_fetch_lock          threading.Lock
_fetch_status        dict  {"running": bool, "last_result": dict | None}
_digest_jobs         dict  {job_id: {"status": "running"|"done"|"error", "result": str}}
_article_translation_jobs  dict  {job_id: {"status", ...}}
_scheduler           APScheduler BackgroundScheduler
```

**Scheduled jobs:**
- Nitter fetch: anchored cron, 11pm SGT (15:00 UTC)
- Digest email send: every 6h at 03:00/09:00/15:00/21:00 SGT

**Route summary:**

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET/POST | `/identify` | public | Email login |
| POST | `/logout` | any | Clear session |
| GET | `/` | user | Main feed |
| GET/POST | `/sources` | user | List + add |
| POST | `/sources/<id>/follow` | user | Toggle follow |
| POST | `/sources/detect` | user | AJAX auto-detect |
| POST | `/sources/<id>/delete` | **admin** | — |
| POST | `/sources/<id>/fetch` | **admin** | Single-source Nitter fetch |
| GET | `/scheduler/status` | user | AJAX |
| POST | `/fetch` | user | Manual fetch → daemon thread |
| GET | `/fetch/status` | user | AJAX |
| GET | `/articles/<id>` | user | JSON |
| POST | `/articles/<id>/delete` | **admin** | — |
| POST | `/articles/<id>/summarize` | user | On-demand summary |
| POST | `/articles/<id>/translate` | user | Spawn thread → job_id |
| GET | `/articles/translate/status/<job_id>` | user | AJAX |
| GET | `/articles/<id>/download/pdf` | user | Playwright PDF |
| POST | `/digest/generate` | user | Spawn thread → job_id |
| GET | `/digest/status/<job_id>` | user | AJAX |
| GET/POST/PUT/DELETE | `/digest/presets*` | user | Preset CRUD (JSON) |
| GET | `/digest` | user | Plain-text export |
| GET | `/digests` | user | Cached digest history |
| GET | `/logs` | user | Fetch log + file tail |
| GET | `/logs/download` | user | Download app.log |
| GET/POST | `/settings` | user | Digest settings + admin |
| POST | `/admin/users/<id>/follows` | **admin** | — |
| POST | `/admin/users/<id>/digest` | **admin** | — |
| GET | `/subscribe` | user | Preset subscription page |
| GET | `/transcript` | user | Transcript UI |
| GET | `/transcript/jobs` | user | AJAX sidebar |
| POST | `/transcript/process` | user | Submit URL + spawn thread |
| GET | `/transcript/status/<job_id>` | user | AJAX poll |
| GET | `/transcript/temp-audio/<token>` | **public** | Serve audio (no auth) |
| POST | `/transcript/<id>/approve` | user | Audio fallback approval |
| POST | `/transcript/<id>/summarize` | user | Spawn summary thread |
| POST | `/transcript/<id>/translate` | user | Spawn translation thread |
| POST | `/transcript/<id>/retry` | user | Retry failed job |
| POST | `/transcript/<id>/delete` | **admin** | — |
| POST | `/transcript/<id>/delete_summary` | **admin** | — |
| GET | `/transcript/download/<job_id>` | user | Download .txt |
| GET | `/transcript/download/<job_id>/pdf` | user | Download PDF |

---

## 2. Cross-Module Call Graph

```
app.py
 ├─ db.*                              (all modules)
 ├─ pipeline.run_fetch_and_summarize()
 ├─ ai_digest.generate_batch_digest()
 ├─ article_summarizer.summarize_single_article()
 ├─ email_digest.build_email_digest()
 ├─ email_sender.send_digest()
 ├─ transcript_worker.process_transcript_job()
 ├─ transcript_worker.continue_audio_transcript()
 ├─ transcript_worker.retry_audio_transcript()
 ├─ transcript_worker.translate_transcript()
 ├─ transcript_worker.generate_transcript_summary()
 └─ fetchers.detect.detect_source()

pipeline.py
 ├─ db.insert_article(), db.get_all_sources(), db.update_source_last_fetched()
 ├─ db.log_fetch_start(), db.log_fetch_finish()
 ├─ fetchers.rss.fetch_rss()
 ├─ fetchers.rss.fetch_youtube()
 ├─ fetchers.rss.fetch_nitter_hybrid()
 ├─ fetchers.web.fetch_web()
 └─ article_summarizer.summarize_new_articles()

ai_digest.py
 ├─ db.get_article_by_id(), db.get_digest_cache(), db.save_digest_cache()
 ├─ db.log_token_usage(), db.update_digest_abstract()
 └─ article_summarizer._chat(), ._get_client(), ._SYSTEM_MESSAGE

article_summarizer.py
 ├─ db.get_unsummarized_articles(), db.update_summary()
 └─ config (Qwen credentials + model)

transcript_worker.py
 ├─ db.get/create/update/set_metadata transcript_job
 └─ config (Qwen, ASR, audio, proxy)

fetchers/rss.py
 └─ fetchers.browser_use_fetcher.enrich_with_playwright()

fetchers/web.py
 └─ fetchers.browser_use_fetcher.fetch_article_with_meta()

fetchers/browser_use_fetcher.py
 └─ config (thresholds, Qwen credentials)

main.py (CLI)
 ├─ pipeline.run_fetch_and_summarize()
 ├─ email_digest.build_email_digest()
 └─ email_sender.send_digest()
```

---

## 3. DB Schema Quick Reference

### `articles`
| Column | Type | Set by |
|---|---|---|
| id | PK | auto |
| source_id | FK→sources | `insert_article()` |
| title | TEXT | `insert_article()` |
| url | TEXT UNIQUE | `insert_article()` |
| content | TEXT | `insert_article()`, re-fetch if thin |
| published_at | TEXT (ISO) | `insert_article()` |
| fetched_at | TEXT | `insert_article()` |
| summary | TEXT | `update_summary()` ← `article_summarizer` |
| digest_abstract | TEXT | `update_digest_abstract()` ← `ai_digest` |
| translated_content | TEXT | `update_article_translation()` ← `app.py` thread |

### `sources`
| Column | Set by |
|---|---|
| id, name, type, url | `upsert_source()` |
| url_filter | `upsert_source()` |
| last_fetched | `update_source_last_fetched()` ← `pipeline` |

### `transcript_jobs`
| Column | Set by |
|---|---|
| job_id, video_url, video_id, mode | `create_transcript_job()` |
| video_title, video_author | `set_transcript_metadata()` |
| status | `update_transcript_job()` |
| transcript | `update_transcript_job()` |
| transcript_zh | `update_transcript_job()` ← translate path |
| summary | `update_transcript_job()` ← summarize path |
| audio_path | `update_transcript_job()` ← audio fallback |
| error_message | `update_transcript_job()` |

### `digest_presets`
| Column | Set by |
|---|---|
| id, user_id, name, source_ids_json | `create_digest_preset()` |
| digest_enabled, digest_frequency_days | `update_digest_preset()` |
| digest_last_sent | `update_preset_last_sent()` ← scheduled job |

---

## 4. Change Ripple Guide

**This is the most important section.** When making any change listed below, always touch *all* the files in the ripple list.

---

### Adding or removing a column in `articles`
1. `db/core.py` — add `ALTER TABLE articles ADD COLUMN ...` in the migrations block (idempotent try/except)
2. `db/articles.py` — update any SELECT that uses `*` or explicit column list; update INSERT if new column is set at write time
3. `db/__init__.py` — no change unless a new function is added
4. `pipeline.py` — if new column is populated during fetch, add to `insert_article()` call
5. `article_summarizer.py` — if new column drives summarization logic
6. `ai_digest.py` — if new column is used in digest generation
7. `templates/index.html` — if new column is displayed in the feed UI
8. `app.py` — if new column is returned from `/articles/<id>` JSON or used in route logic

---

### Adding or removing a column in `sources`
1. `db/core.py` — ALTER TABLE migration
2. `db/sources.py` — update SELECT/INSERT/UPDATE
3. `db/__init__.py` — if new function added
4. `app.py` — `/sources` GET (passes source data to template) and POST (reads form field)
5. `templates/sources.html` — display/input field

---

### Adding or removing a column in `transcript_jobs`
1. `db/core.py` — ALTER TABLE migration
2. `db/transcripts.py` — update `update_transcript_job()`, `get_transcript_job()`, column list
3. `transcript_worker.py` — where value is set during processing
4. `app.py` — `/transcript/status/<id>` JSON response; download helpers
5. `templates/transcript.html` — if displayed in UI

---

### Adding or removing a column in `digest_presets`
1. `db/core.py` — ALTER TABLE migration
2. `db/digests.py` — update relevant functions
3. `app.py` — preset CRUD routes (`/digest/presets*`)
4. `templates/index.html` or `templates/subscribe.html` — if displayed

---

### Adding a new public function to a `db/` module
1. The module file (implement it)
2. `db/__init__.py` — add to re-exports

---

### Adding a new LLM call / operation
1. `config.py` — add model constant if not using existing one
2. The calling module (implement call)
3. `db/digests.py` `log_token_usage()` — call it with the new operation name
4. `CLAUDE.md` — if it's a new model constant, add to the Config table

---

### Adding a new transcript job status
1. `db/transcripts.py` — add to the docstring list of valid statuses
2. `transcript_worker.py` — set the new status via `update_transcript_job()`
3. `app.py` — `/transcript/status/<id>` handler (check if status needs special response)
4. `templates/transcript.html` — display logic for the new status

---

### Adding a new background job type (non-persistent, in-memory)
1. `app.py` — new module-level dict `_<name>_jobs = {}`; new spawn route; new status route
2. Template — AJAX poll + display

### Adding a new background job type (persistent, survives restart)
1. `db/core.py` — new table in `init_db()`
2. New `db/<name>.py` — CRUD functions
3. `db/__init__.py` — re-export
4. `app.py` — spawn route + status route + startup state reset (for stuck jobs)
5. Template — AJAX poll + display

---

### Adding a new source type
1. `fetchers/` — new fetcher file (follow `rss.py` pattern)
2. `pipeline.py` — add routing branch for the new type
3. `fetchers/detect.py` — add detection logic
4. `db/sources.py` — if type needs validation (currently free-text)
5. `templates/sources.html` — add option to type selector

---

### Adding a new route
1. `app.py` — route handler
2. Template — link/form if user-visible
3. `templates/base.html` — if new top-level nav item

---

### Adding a new config constant
1. `config.py` — define with env var fallback
2. `CLAUDE.md` — add to the Config constants table
3. `CODEMAP.md` — add to Module Registry → config.py section
4. `.env.example` — if user must supply a value

---

### Changing a public function signature in `db/`
1. The `db/` module file
2. All callers — use Grep to find every call site: `grep -r "db.<function_name>" .`

### Changing `article_summarizer._chat()` or `_get_client()`
These are imported directly by `ai_digest.py`. Changes affect both files.

### Changing `pipeline.run_fetch_and_summarize()` signature
Callers: `app.py` (manual fetch thread + scheduled Nitter fetch), `main.py` (CLI).

### Changing `ai_digest.generate_batch_digest()` signature
Callers: `app.py` digest job thread.

---

## 5. Template ↔ Route Bindings

| Template | Route(s) that render it | Key variables passed |
|---|---|---|
| `index.html` | `GET /` | `articles`, `sources`, `presets`, `followed_ids`, current user |
| `sources.html` | `GET/POST /sources` | `sources` (with `followed` flag), `is_admin` |
| `transcript.html` | `GET /transcript` | `jobs` (sidebar), `job` (active), `is_admin` |
| `logs.html` | `GET /logs` | `fetch_log`, `log_tail` |
| `settings.html` | `GET/POST /settings` | `user`, `token_summary`, `all_users` (admin), `presets` |
| `identify.html` | `GET/POST /identify` | `error` |
| `base.html` | all (via `{% extends %}`) | `current_user`, nav items |

---

## 6. How to Maintain This File

After completing any code change:
1. If a **public function signature changed** → update the Module Registry entry for that file
2. If a **new cross-module import was added** → update the Call Graph
3. If a **DB column was added/removed** → update DB Schema Quick Reference
4. If a **new change pattern was discovered** → add or extend the Change Ripple Guide
5. If a **new config constant was added** → update the config.py Module Registry entry

**Do not** rewrite sections that didn't change. Targeted updates only.
