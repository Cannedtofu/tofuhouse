# Project Status
_Last updated: 2026-07-07_

## Shipped & Stable

### News Feed Pipeline
- RSS/Atom fetching with URL-based dedup; richer content wins on re-fetch
- Nitter/X scraping with HTML pagination
- Web source fetching with link discovery plus full-content extraction
- Topic workflow under `资讯`: OpenAI-SDK-based entity tracking, currently focused on Web discovery
- Topic `web` discovery currently uses a Google API-backed search path
- Topic `web` discovery now limits itself to 3 query variants, paces requests, and logs per-query results for inspection
- Topic items stored separately from source articles and rendered in the same feed UI

### Summarization & Digest
- Per-article summaries and one-off AI digests for source-backed articles
- Digest generation remains source/article based and is not coupled to topic storage

### YouTube Transcript
- Transcript fetch pipeline with caption-first flow and audio fallback
- Topic workflow reuses transcript extraction when a YouTube result is selected as the primary readable body

### Web UI & Auth
- Flask + Bootstrap UI with `资讯`, `来源`, `话题`, `转录`, `数据`, and `日志`
- Topic management page at `/topics`
- `资讯` can now render both followed sources and followed topics

### Infrastructure
- SQLite with WAL mode and in-place schema migrations
- Separate scheduled topic fetch job alongside the existing source scheduler

## In Progress
- Tightening topic-result precision and ranking heuristics

## Known Issues
- Topic discovery depends on valid Google API credentials and search engine configuration
- Topic `youtube` and `x` discovery are temporarily disabled while Web discovery is being stabilized
- Topic items do not yet participate in the existing digest preset system

## Next
- Improve topic editing UX
- Add richer provenance display and confidence tuning for topic items
