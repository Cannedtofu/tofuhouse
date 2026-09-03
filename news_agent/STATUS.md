# Project Status
_Last updated: 2026-09-03_

## Shipped & Stable

### News Feed Pipeline
- RSS/Atom fetching with URL-based dedup; richer content wins on re-fetch.
- Nitter/X scraping with HTML pagination.
- Web source fetching with link discovery plus full-content extraction.
- Web source fetching normalizes article URLs, skips extracted error pages, and avoids LLM-generated 404 content.
- Topic workflow under `资讯` is currently focused on YouTube discovery.
- Topic `web` and `x` discovery are disabled in the UI while YouTube is stabilized.
- Topic YouTube discovery returns newest-first candidate videos, enriches full YouTube metadata/description, skips videos under 20 minutes, then performs AI relevance filtering.
- Topic dedupe is scoped to topic storage only; normal RSS/YouTube `articles` no longer block topic ingestion.
- Topic items are stored separately from source articles and rendered in the same feed UI.
- Topic feed excludes X/Twitter/Nitter URLs from topic discovery and feed rendering.
- The `资讯` source/topic filters preserve explicit empty selections, so users can show only topics, only sources, or neither without falling back to defaults.

### Summarization & Digest
- Per-article summaries and one-off AI digests for source-backed articles.
- Independent `新增信息流日报` raw-feed digest covers selected topics plus selected RSS/YouTube sources.
- Raw-feed digest batches items for Qwen and rewrites each item into a concise Simplified Chinese intro.
- Admin users can manually trigger each digest type from subscription/settings workflows.

### YouTube Transcript
- Transcript fetch pipeline with caption-first flow and audio fallback.
- Transcript delete actions update the UI dynamically without a full page refresh.
- Transcript paste polling uses lightweight status responses while processing, avoiding repeated large transcript payloads.
- Transcript paste submissions upload in chunks to avoid nginx request-body timeouts on large transcripts.
- Transcript paste chunk transfer uses bodyless header chunks with retry to avoid request-body stalls.
- App-level request and pasted-transcript chunk diagnostics are written to `logs/app.log` for the Logs page.
- YouTube audio download now retries transient yt-dlp SSL/API-page failures and requires a current yt-dlp release line.

### Conference Calls
- Added a `电话会` tab backed by a separate `conferences.db` SQLite database.
- Comein roadshow refresh uses Playwright to load and scroll `https://www.comein.cn/roadshow/home/all`, reads rendered `.roadshow-list-item` Vue card data, and stops after seeing meetings outside the next 5 days or when the list stops growing.
- Users can maintain a per-user conference topic keyword list; Qwen matches future conference titles to topics and the UI groups matched meetings by topic.
- Conference matching now caches per-user, per-conference, per-topic labels in `conference_matches`; later refreshes only call Qwen for missing labels.
- Admin users can manually force relabel the current 5-day conference window from the `电话会` tab.
### Web UI & Auth
- Flask + Bootstrap UI with `资讯`, `来源`, `话题`, `转录`, `数据`, and `日志`.
- Topic management page at `/topics`.
- `资讯` can render both followed sources and followed topics.
- Admin deletes on `资讯` update the page dynamically without a full reload.

### Data Dashboard
- Added a POP MART YouTube dashboard panel under `数据` with a weekly trend line chart, latest-100/current-history CSV downloads, weekly scheduled refresh, admin manual refresh, retrying/rate-limited yt-dlp detail fetches, and first-crawl baseline exclusion from chart data.
- POP MART YouTube weekly new-video counts now use the interval between consecutive crawl snapshots, matching the weekly view-delta window instead of only counting videos published on the Monday crawl date.
- POP MART YouTube dashboard reports are rebuilt from existing snapshots on dashboard load so cached line-chart data reflects calculation fixes immediately without a new YouTube crawl.
- LLM Token Expenditure Index now fetches Silicon Data's LLM Token, Open LLM, and Proprietary LLM series from the plural token-indexes portal, stores all three in the dashboard Excel, and refreshes stale reports automatically.
### Infrastructure
- Outbound WeCom notification foundation is ready as a distinct subproject under `docs/wecom-assistant`: group robot webhook channel, optional self-built app client, env template, Docker files, README, server checklist, roadmap, acceptance scripts, and `scripts/send_raw_feed_wecom.py` for sending raw-feed digests to WeCom.
- SQLite with WAL mode and in-place schema migrations.
- Separate scheduled topic fetch job alongside the existing source scheduler.
- Separate scheduled raw-feed digest job alongside the existing AI digest scheduler.

## In Progress
- PDF tool translation uses large structured batches while preserving image/text block order.
- PDF tool now persists completed bilingual PDFs and shows a refresh-safe recent-files list.
- Added a 工具 tab for uploaded-PDF paragraph translation plus pasted-transcript cleanup/translation.
- Added a Q4 Inc earnings-call tool path under 工具: local validation logs into Q4 attendee pages, captures the recording, extracts audio-only `.m4a`, then hands it to the existing diarization transcript pipeline and Chinese translation flow.

- Tightening topic-result precision, ranking heuristics, and date semantics for topic items.

## Known Issues
- Real WeCom webhook acceptance test is pending `WECOM_WEBHOOK_URL` in server `.env`; self-built app acceptance remains deferred because trusted-domain/IP setup is heavier.
- Topic auto-fetch can still surface older videos if YouTube search returns relevant historical items; decide whether scheduled topic fetch should enforce a recent published-date window.
- Topic `web` and `x` discovery remain disabled while YouTube discovery is stabilized.
- Topic items do not yet participate in the existing AI digest preset system.

## Next
- Decide whether `资讯` topic date filtering should use `fetched_at`, `published_at`, or a visible toggle.
- Improve topic editing UX.
- Add richer provenance display and confidence tuning for topic items.
