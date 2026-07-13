"""OpenAI-SDK-based workflow for topic tracking."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

import db
from article_summarizer import _get_client
from config import (
    GOOGLE_SEARCH_API_KEY,
    GOOGLE_SEARCH_ENGINE_ID,
    QWEN_SUMMARY_MODEL,
    TOPIC_DEFAULT_CHANNELS,
    TOPIC_MAX_RESULTS_PER_QUERY,
    TOPIC_MIN_CONFIDENCE,
    TOPIC_QUERY_SLEEP_SECONDS,
)
from fetchers.browser_use_fetcher import fetch_article_with_meta
from transcript_worker import _fetch_transcript_fast, extract_video_id

logger = logging.getLogger(__name__)

_INTERVIEW_TERMS = ("interview", "remarks", "statement", "speech", "keynote", "talk", "podcast", "q&a")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "with",
    "interview", "remarks", "statement", "speech", "keynote", "talk", "podcast", "qa", "q", "a",
}
_TOPIC_SYSTEM = (
    "You identify whether a search result is a first-party public interview, speech, "
    "statement, or direct remarks by the requested entity. Respond with compact JSON only."
)
_QUERY_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "topic_query_results.jsonl")


def _normalize_url(url: str) -> str:
    url = url.strip()
    if "youtube.com/watch" in url or "youtu.be/" in url:
        vid = extract_video_id(url)
        return f"https://www.youtube.com/watch?v={vid}" if vid else url
    return url.rstrip("/")


def _build_queries(topic: dict) -> list[str]:
    base_names = [topic["name"], *topic.get("aliases", [])]
    cleaned_names: list[str] = []
    seen = set()
    for alias in base_names:
        alias = alias.strip()
        if not alias:
            continue
        key = alias.lower()
        if key not in seen:
            seen.add(key)
            cleaned_names.append(alias)

    primary = cleaned_names[0]
    secondary = cleaned_names[1] if len(cleaned_names) > 1 else f'"{primary}" interview'
    tertiary = f'"{primary}" remarks'
    if secondary.lower() == primary.lower():
        secondary = f'"{primary}" interview'

    queries = [primary, secondary, tertiary]
    deduped: list[str] = []
    seen.clear()
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped[:3]


def _log_query_results(
    topic: dict,
    channel: str,
    query: str,
    results: list[dict[str, Any]],
    note: str | None = None,
) -> None:
    os.makedirs(os.path.dirname(_QUERY_LOG_PATH), exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topic_id": topic["id"],
        "topic_name": topic["name"],
        "channel": channel,
        "query": query,
        "result_count": len(results),
        "note": note,
        "results": results,
    }
    with open(_QUERY_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _search_google_web(query: str, limit: int = TOPIC_MAX_RESULTS_PER_QUERY) -> tuple[list[dict[str, Any]], str | None]:
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        logger.warning("[topic] google api credentials missing")
        return [], "missing_google_api_credentials"

    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_SEARCH_API_KEY,
                "cx": GOOGLE_SEARCH_ENGINE_ID,
                "q": query,
                "num": min(limit, 10),
                "hl": "en",
                "safe": "off",
            },
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[topic] google api search failed for %s: %s", query, exc)
        return [], f"google_api_request_failed: {exc}"

    try:
        payload = resp.json()
    except Exception as exc:
        logger.warning("[topic] google api json parse failed for %s: %s", query, exc)
        return [], f"google_api_invalid_json: {exc}"

    items = payload.get("items") or []
    if not items:
        return [], "google_api_no_items"

    results: list[dict[str, Any]] = []
    for item in items[:limit]:
        link = str(item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        results.append({
            "title": str(item.get("title") or "").strip() or link,
            "url": _normalize_url(link),
            "snippet": str(item.get("snippet") or "").strip(),
        })
    return results, None


def _date_in_range(date_str: str | None, date_from: str | None, date_to: str | None) -> bool:
    if not date_str:
        return True
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if date_from and dt < datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc):
            return False
        if date_to and dt > datetime.fromisoformat(date_to + "T23:59:59").replace(tzinfo=timezone.utc):
            return False
    except Exception:
        return True
    return True


def _heuristic_score(topic: dict, title: str, content: str, platform: str) -> float:
    haystack = f"{title} {content}".lower()
    aliases = [topic["name"], *topic.get("aliases", [])]
    alias_hits = sum(1 for alias in aliases if alias and alias.lower() in haystack)
    term_hits = sum(1 for term in _INTERVIEW_TERMS if term in haystack)
    score = 0.15 * alias_hits + 0.12 * term_hits
    if platform == "youtube":
        score += 0.15
    elif platform == "x":
        score += 0.08
    if any(term in haystack for term in ("said", "says", "speaking", "on stage")):
        score += 0.08
    return min(score, 0.95)


def _slug_tokens(text: str, topic: dict) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    alias_tokens = set()
    for alias in [topic["name"], *topic.get("aliases", [])]:
        alias_tokens.update(re.findall(r"[a-z0-9]+", alias.lower()))
    tokens = []
    for tok in cleaned.split():
        if tok in _STOPWORDS or tok in alias_tokens:
            continue
        tokens.append(tok)
    return tokens[:12]


def _canonical_key(topic: dict, title: str, published_at: str | None) -> str:
    date_key = (published_at or "")[:10]
    body = " ".join(_slug_tokens(title, topic)) or re.sub(r"\s+", " ", title.lower()).strip()[:80]
    return hashlib.sha1(f"{topic['id']}|{date_key}|{body}".encode("utf-8")).hexdigest()


def _search_web(topic: dict, queries: list[str]) -> list[dict[str, Any]]:
    if "web" not in (topic.get("channels") or TOPIC_DEFAULT_CHANNELS):
        return []
    results: list[dict[str, Any]] = []
    seen = set()
    capped_queries = queries[:3]
    for idx, query in enumerate(capped_queries):
        query_results, note = _search_google_web(query)
        _log_query_results(topic, "web", query, query_results, note=note)
        for item in query_results:
            url = item["url"]
            if url in seen:
                continue
            seen.add(url)
            parsed = urlparse(url)
            if "youtube.com" in parsed.netloc or "x.com" in parsed.netloc or "twitter.com" in parsed.netloc:
                continue
            item["platform"] = "web"
            item["source_label"] = parsed.netloc
            results.append(item)
        if idx < len(capped_queries) - 1 and TOPIC_QUERY_SLEEP_SECONDS > 0:
            time.sleep(TOPIC_QUERY_SLEEP_SECONDS)
    return results


def _search_youtube(topic: dict, queries: list[str]) -> list[dict[str, Any]]:
    logger.info("[topic] youtube discovery disabled for now")
    return []


def _search_x(topic: dict, queries: list[str]) -> list[dict[str, Any]]:
    logger.info("[topic] x discovery disabled for now")
    return []


def _classify_with_openai(topic: dict, title: str, content: str, platform: str) -> tuple[bool, float, str]:
    heuristic = _heuristic_score(topic, title, content, platform)
    client = _get_client()
    prompt = {
        "topic_name": topic["name"],
        "aliases": topic.get("aliases", []),
        "platform": platform,
        "title": title[:300],
        "content_excerpt": (content or "")[:2000],
        "instruction": (
            "Decide whether this is a first-party public interview, speech, statement, "
            "or direct remarks by the entity. Return JSON with keys: keep (bool), "
            "confidence (0..1), reason (short string)."
        ),
    }
    try:
        resp = client.chat.completions.create(
            model=QWEN_SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": _TOPIC_SYSTEM},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=180,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        keep = bool(data.get("keep"))
        confidence = float(data.get("confidence", heuristic))
        reason = str(data.get("reason", "")).strip()
        return keep, max(0.0, min(confidence, 1.0)), reason
    except Exception as exc:
        logger.warning("[topic] OpenAI SDK classification failed for %s: %s", title[:80], exc)
        return heuristic >= TOPIC_MIN_CONFIDENCE, heuristic, "heuristic fallback"


def _enrich_candidates(
    topic: dict,
    candidates: list[dict[str, Any]],
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    seen_urls = set()
    for cand in candidates:
        url = _normalize_url(cand["url"])
        if url in seen_urls:
            continue
        seen_urls.add(url)

        platform = cand["platform"]
        title = cand["title"]
        content = cand.get("snippet", "")
        published_at = cand.get("published_at")
        primary_url = url

        if platform == "web":
            try:
                article_content, article_date = fetch_article_with_meta(url)
            except Exception as exc:
                logger.warning("[topic] web fetch failed for %s: %s", url, exc)
                article_content, article_date = None, None
            if article_content:
                content = article_content
                published_at = article_date
                for line in article_content.splitlines():
                    line = line.strip().lstrip("#").strip()
                    if line:
                        title = line
                        break
        elif platform == "youtube":
            transcript = _fetch_transcript_fast(url)
            if transcript:
                content = transcript
        elif platform == "x":
            content = cand.get("snippet") or title

        if not _date_in_range(published_at, date_from, date_to):
            continue

        keep, confidence, reason = _classify_with_openai(topic, title, content or "", platform)
        if not keep or confidence < TOPIC_MIN_CONFIDENCE:
            logger.info("[topic] filtered out %s (%s)", title[:120], reason)
            continue

        enriched.append({
            "canonical_key": _canonical_key(topic, title, published_at),
            "title": title or primary_url,
            "url": primary_url,
            "content": content or cand.get("snippet") or "",
            "published_at": published_at,
            "primary_platform": platform,
            "confidence": confidence,
            "supporting_sources": [{
                "platform": platform,
                "source_label": cand.get("source_label"),
                "url": primary_url,
                "title": cand.get("title") or title,
                "content_snippet": cand.get("snippet") or (content[:280] if content else ""),
                "published_at": published_at,
                "is_primary": True,
            }],
        })
    return enriched


def _dedupe_and_persist(topic: dict, enriched_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    for cand in enriched_candidates:
        existing = grouped.get(cand["canonical_key"])
        if not existing or len(cand.get("content") or "") > len(existing.get("content") or ""):
            grouped[cand["canonical_key"]] = cand
        elif existing:
            existing["supporting_sources"].extend(cand.get("supporting_sources", []))

    for cand in grouped.values():
        item_id, is_new = db.upsert_topic_item(
            topic_id=topic["id"],
            canonical_key=cand["canonical_key"],
            title=cand["title"],
            url=cand["url"],
            content=cand["content"],
            published_at=cand.get("published_at"),
            primary_platform=cand["primary_platform"],
            confidence=cand["confidence"],
            supporting_sources=cand.get("supporting_sources", []),
        )
        persisted.append({"id": item_id, "new": is_new, "title": cand["title"]})

    db.update_topic_last_fetched(topic["id"])
    return persisted


def _run_topic_pipeline(topic: dict, date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    queries = _build_queries(topic)
    candidates = [
        *_search_web(topic, queries),
        *_search_youtube(topic, queries),
        *_search_x(topic, queries),
    ]
    enriched = _enrich_candidates(topic, candidates, date_from, date_to)
    return _dedupe_and_persist(topic, enriched)


def run_topic_fetch(
    topic_ids: list[int] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    topics = db.get_all_topics(active_only=True)
    if topic_ids:
        wanted = set(topic_ids)
        topics = [topic for topic in topics if topic["id"] in wanted]
    if not topics:
        return {"total_new": 0, "sources": []}

    total_new = 0
    results: list[dict[str, Any]] = []
    for topic in topics:
        logger.info("[topic] fetching topic %s", topic["name"])
        persisted = _run_topic_pipeline(topic, date_from, date_to)
        new_count = sum(1 for item in persisted if item["new"])
        total_new += new_count
        results.append({
            "name": topic["name"],
            "type": "topic",
            "new": new_count,
            "fetched": len(persisted),
            "error": None,
        })

    return {"total_new": total_new, "sources": results}
