# Project Status
_Last updated: 2026-08-05_

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

### Web UI & Auth
- Flask + Bootstrap UI with `资讯`, `来源`, `话题`, `转录`, `数据`, and `日志`.
- Topic management page at `/topics`.
- `资讯` can render both followed sources and followed topics.
- Admin deletes on `资讯` update the page dynamically without a full reload.

### Infrastructure
- SQLite with WAL mode and in-place schema migrations.
- Separate scheduled topic fetch job alongside the existing source scheduler.
- Separate scheduled raw-feed digest job alongside the existing AI digest scheduler.

## In Progress
- PDF tool now persists completed bilingual PDFs and shows a refresh-safe recent-files list.
- Added a 工具 tab for uploaded-PDF paragraph translation plus pasted-transcript cleanup/translation.

- Tightening topic-result precision, ranking heuristics, and date semantics for topic items.

## Known Issues
- Topic auto-fetch can still surface older videos if YouTube search returns relevant historical items; decide whether scheduled topic fetch should enforce a recent published-date window.
- Topic `web` and `x` discovery remain disabled while YouTube discovery is stabilized.
- Topic items do not yet participate in the existing AI digest preset system.

## Next
- Decide whether `资讯` topic date filtering should use `fetched_at`, `published_at`, or a visible toggle.
- Improve topic editing UX.
- Add richer provenance display and confidence tuning for topic items.
