# Project Status
_Last updated: —_

---

## Shipped & Stable

### News Feed Pipeline
- RSS/Atom fetching with URL-based dedup; richer content wins on re-fetch
- Nitter RSS (X.com) scraping via `fetchers/nitter_html.py` with HTML pagination
- APScheduler background fetch: clock-anchored daily at 11pm SGT, status badge in UI
- Web source fetching: LLM-assisted link discovery → Playwright content extraction
- Two-tier content extraction in `browser_use_fetcher.py`:
  - Tier 1: Playwright (headed, off-screen) → trafilatura → Markdown + images
  - Tier 2: browser-use LLM agent (qwen3-vl-flash) if Tier 1 < 300 chars
- Lazy-load image handling: scroll-to-bottom before extraction, Substack data-attrs support
- Web source two-stage date filtering: listing-page pre-filter → article-page authoritative check
- Thin-content re-fetch: articles < 200 words are not skipped on next run
- Source `url_filter` for restricting feeds (e.g. OpenAI blog → `/index/` only)
- Source auto-detection from raw URL: `fetchers/detect.py`

### Summarization & Digest
- Per-article 2–3 sentence blurbs via `article_summarizer.py` (qwen-plus)
- AI digest (`ai_digest.py`): importance classification → abstracts → cross-source Big Picture
- Nitter digest path: original-tweet summaries + retweet signal handled separately
- Digest cached by sorted article ID hash; re-submitting same set is instant
- Plain-text markdown digest for email via `email_digest.py` + SMTP delivery

### YouTube Transcript
- Fast path: yt-dlp subtitle download only (no audio) — English, Chinese, then all languages
- Audio fallback with explicit user approval gate before download
- No-diarization mode: paraformer-v2 ASR, single `Recognition.call()` (no chunking, up to 12h)
- Diarization mode: `diarization_enabled=True`, consistent `[Speaker A]` IDs across full video
- AI summary: single Qwen call ≤ 12k chars; chunk-then-synthesize for longer transcripts
- Job persistence in `transcript_jobs` SQLite table — survives app restarts
- Frontend polls `/transcript/status/<job_id>` every 3s; `.txt` download available

### Web UI & Auth
- Flask + Bootstrap 5, Chinese nav: 资讯 来源 转录 日志 设置
- Two-panel feed layout with inline AI digest panel
- Source management: add, follow/unfollow, auto-detect type, admin-only delete (403 for others)
- Per-user token usage stats in `/settings`
- Email-only login; `EMAIL_WHITELIST` for access control; single `ADMIN_EMAIL` for admin ops
- Fetch triggered via AJAX (`POST /fetch`), polled via `/fetch/status`

### Infrastructure
- SQLite (`news.db`) with WAL mode, idempotent `ALTER TABLE` migrations
- All background work via `daemon=True` threads; fetch protected by `_fetch_lock`
- Digest jobs in-memory (UUID-keyed dict); transcript jobs in DB (restartable)
- GitHub Actions daily report at 07:00 UTC; `news.db` artifact persisted 30 days
- Systemd + gunicorn on Alibaba ECS HK; deploy via `scripts/deploy.sh`

---

## In Progress
<!-- What are you actively working on? -->

---

## Known Issues
<!-- Bugs, rough edges, or things that sometimes break. -->

---

## Next
<!-- Planned work after current task is done. -->
