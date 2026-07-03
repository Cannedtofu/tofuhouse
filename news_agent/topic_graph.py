"""LangGraph-based workflow for topic tracking."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

import db
from config import TOPIC_DEFAULT_CHANNELS, TOPIC_MAX_RESULTS_PER_QUERY, TOPIC_MIN_CONFIDENCE
from fetchers.browser_use_fetcher import fetch_article_with_meta
from transcript_worker import _fetch_transcript_fast, extract_video_id

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (compatible; news-agent-topic/1.0)"}
_INTERVIEW_TERMS = ("interview", "remarks", "statement", "speech", "keynote", "talk", "podcast", "q&a")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "with",
    "interview", "remarks", "statement", "speech", "keynote", "talk", "podcast", "qa", "q", "a",
}


class TopicState(TypedDict, total=False):
    topic: dict
    date_from: str | None
    date_to: str | None
    queries: list[str]
    web_candidates: list[dict[str, Any]]
    youtube_candidates: list[dict[str, Any]]
    x_candidates: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    enriched_candidates: list[dict[str, Any]]
    persisted: list[dict[str, Any]]


def _normalize_url(url: str) -> str:
    url = url.strip()
    if "youtube.com/watch" in url:
        vid = extract_video_id(url)
        return f"https://www.youtube.com/watch?v={vid}" if vid else url
    return url.rstrip("/")


def _build_queries(topic: dict) -> list[str]:
    base_names = [topic["name"], *topic.get("aliases", [])]
    queries: list[str] = []
    for alias in base_names:
        alias = alias.strip()
        if not alias:
            continue
        queries.append(alias)
        for term in _INTERVIEW_TERMS:
            queries.append(f'"{alias}" {term}')
    deduped: list[str] = []
    seen = set()
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped


def _search_duckduckgo(query: str, site: str | None = None, limit: int = TOPIC_MAX_RESULTS_PER_QUERY) -> list[dict[str, Any]]:
    q = query if not site else f"{query} site:{site}"
    url = f"https://duckduckgo.com/html/?q={quote_plus(q)}"
    try:
        resp = requests.get(url, headers=_UA, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[topic] search failed for %s: %s", q, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, Any]] = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        if not href.startswith("http"):
            continue
        snippet_el = result.select_one(".result__snippet")
        title = link.get_text(" ", strip=True)
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({
            "title": title,
            "url": _normalize_url(href),
            "snippet": snippet,
        })
        if len(results) >= limit:
            break
    return results


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


def _score_candidate(topic: dict, title: str, content: str, platform: str) -> float:
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


def _expand_queries(state: TopicState) -> TopicState:
    return {"queries": _build_queries(state["topic"])}


def _search_web(state: TopicState) -> TopicState:
    if "web" not in (state["topic"].get("channels") or TOPIC_DEFAULT_CHANNELS):
        return {"web_candidates": []}
    results: list[dict[str, Any]] = []
    seen = set()
    for query in state["queries"][:8]:
        for item in _search_duckduckgo(query):
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
    return {"web_candidates": results}


def _search_youtube(state: TopicState) -> TopicState:
    if "youtube" not in (state["topic"].get("channels") or TOPIC_DEFAULT_CHANNELS):
        return {"youtube_candidates": []}
    results: list[dict[str, Any]] = []
    seen = set()
    for query in state["queries"][:6]:
        for item in _search_duckduckgo(query, site="youtube.com"):
            if "youtube.com/watch" not in item["url"] and "youtu.be/" not in item["url"]:
                continue
            vid = extract_video_id(item["url"])
            if not vid or vid in seen:
                continue
            seen.add(vid)
            item["url"] = f"https://www.youtube.com/watch?v={vid}"
            item["platform"] = "youtube"
            item["source_label"] = "YouTube"
            results.append(item)
    return {"youtube_candidates": results}


def _search_x(state: TopicState) -> TopicState:
    if "x" not in (state["topic"].get("channels") or TOPIC_DEFAULT_CHANNELS):
        return {"x_candidates": []}
    results: list[dict[str, Any]] = []
    seen = set()
    for query in state["queries"][:6]:
        for item in _search_duckduckgo(query, site="x.com"):
            if "x.com/" not in item["url"] and "twitter.com/" not in item["url"]:
                continue
            url = item["url"].replace("twitter.com/", "x.com/")
            if "/status/" not in url or url in seen:
                continue
            seen.add(url)
            item["url"] = url
            item["platform"] = "x"
            item["source_label"] = "X"
            results.append(item)
    return {"x_candidates": results}


def _merge_candidates(state: TopicState) -> TopicState:
    combined = [
        *state.get("web_candidates", []),
        *state.get("youtube_candidates", []),
        *state.get("x_candidates", []),
    ]
    return {"candidates": combined}


def _enrich_candidates(state: TopicState) -> TopicState:
    topic = state["topic"]
    enriched: list[dict[str, Any]] = []
    seen_urls = set()
    for cand in state.get("candidates", []):
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
            published_at = published_at or None
        elif platform == "x":
            content = cand.get("snippet") or title

        if not _date_in_range(published_at, state.get("date_from"), state.get("date_to")):
            continue
        score = _score_candidate(topic, title, content or "", platform)
        if score < TOPIC_MIN_CONFIDENCE:
            continue
        enriched.append({
            "canonical_key": _canonical_key(topic, title, published_at),
            "title": title or primary_url,
            "url": primary_url,
            "content": content or cand.get("snippet") or "",
            "published_at": published_at,
            "primary_platform": platform,
            "confidence": score,
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
    return {"enriched_candidates": enriched}


def _dedupe_and_persist(state: TopicState) -> TopicState:
    topic = state["topic"]
    persisted: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    for cand in state.get("enriched_candidates", []):
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
    return {"persisted": persisted}


def _compile_graph():
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(TopicState)
    graph.add_node("expand_queries", _expand_queries)
    graph.add_node("search_web", _search_web)
    graph.add_node("search_youtube", _search_youtube)
    graph.add_node("search_x", _search_x)
    graph.add_node("merge_candidates", _merge_candidates)
    graph.add_node("enrich_candidates", _enrich_candidates)
    graph.add_node("dedupe_and_persist", _dedupe_and_persist)

    graph.add_edge(START, "expand_queries")
    graph.add_edge("expand_queries", "search_web")
    graph.add_edge("expand_queries", "search_youtube")
    graph.add_edge("expand_queries", "search_x")
    graph.add_edge("search_web", "merge_candidates")
    graph.add_edge("search_youtube", "merge_candidates")
    graph.add_edge("search_x", "merge_candidates")
    graph.add_edge("merge_candidates", "enrich_candidates")
    graph.add_edge("enrich_candidates", "dedupe_and_persist")
    graph.add_edge("dedupe_and_persist", END)
    return graph.compile()


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

    graph = _compile_graph()
    total_new = 0
    results: list[dict[str, Any]] = []

    for topic in topics:
        logger.info("[topic] fetching topic %s", topic["name"])
        state = graph.invoke({
            "topic": topic,
            "date_from": date_from,
            "date_to": date_to,
        })
        new_count = sum(1 for item in state.get("persisted", []) if item["new"])
        total_new += new_count
        results.append({
            "name": topic["name"],
            "type": "topic",
            "new": new_count,
            "fetched": len(state.get("persisted", [])),
            "error": None,
        })

    return {"total_new": total_new, "sources": results}
