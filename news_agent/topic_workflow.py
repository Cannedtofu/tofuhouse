"""OpenAI-SDK-based workflow for topic tracking."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

import db
from article_summarizer import _get_client
from config import (
    GEMINI_API_KEY,
    QWEN_SUMMARY_MODEL,
    SOCKS_PROXY,
    TOPIC_DEFAULT_CHANNELS,
    TOPIC_GOOGLE_SEARCH_MODEL,
    TOPIC_MAX_RESULTS_PER_QUERY,
    TOPIC_MIN_CONFIDENCE,
    TOPIC_QUERY_SLEEP_SECONDS,
    YOUTUBE_COOKIES_FILE,
)
from fetchers.browser_use_fetcher import fetch_article_with_meta
from transcript_worker import extract_video_id

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
_GEMINI_SEARCH_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _is_twitter_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("mobile."):
        host = host[7:]
    return (
        host in {"x.com", "twitter.com", "t.co", "nitter.net"}
        or host.endswith(".twitter.com")
        or host.startswith("nitter.")
    )

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


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None

    candidates = [raw]
    if "```" in raw:
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
        candidates = fenced + candidates

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(candidate[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return None


def _search_google_web(query: str, limit: int = TOPIC_MAX_RESULTS_PER_QUERY) -> tuple[list[dict[str, Any]], str | None]:
    if not GEMINI_API_KEY:
        logger.warning("[topic] gemini api key missing")
        return [], "missing_gemini_api_key"

    prompt = (
        "Use Google Search to find public first-party interviews, speeches, statements, or direct remarks "
        f"for this query: {query}\n"
        "Return compact JSON only with this schema: "
        '{"candidates":[{"title":"string","url":"https://...","snippet":"string"}]}. '
        f"Include at most {limit} candidates. Prefer direct source pages, interview pages, transcripts, "
        "and reputable writeups that quote the person directly. Exclude YouTube, X, and Twitter URLs."
    )

    try:
        resp = requests.post(
            _GEMINI_SEARCH_URL,
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": TOPIC_GOOGLE_SEARCH_MODEL,
                "input": prompt,
                "tools": [{"type": "google_search"}],
            },
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[topic] gemini google search failed for %s: %s", query, exc)
        return [], f"gemini_google_search_failed: {exc}"

    try:
        payload = resp.json()
    except Exception as exc:
        logger.warning("[topic] gemini google search json parse failed for %s: %s", query, exc)
        return [], f"gemini_google_search_invalid_json: {exc}"

    steps = payload.get("steps") or []
    model_output_blocks: list[dict[str, Any]] = []
    executed_queries: list[str] = []
    for step in steps:
        if step.get("type") == "google_search_call":
            executed_queries.extend(step.get("arguments", {}).get("queries") or [])
        if step.get("type") == "model_output":
            model_output_blocks.extend(step.get("content") or [])

    output_text = payload.get("output_text") or ""
    parsed_output = _extract_json_object(output_text)

    citations_by_url: dict[str, dict[str, str]] = {}
    for block in model_output_blocks:
        if block.get("type") != "text":
            continue
        block_text = str(block.get("text") or "")
        if parsed_output is None:
            parsed_output = _extract_json_object(block_text)
        for annotation in block.get("annotations") or []:
            if annotation.get("type") != "url_citation":
                continue
            url = _normalize_url(str(annotation.get("url") or ""))
            if not url:
                continue
            start_idx = int(annotation.get("start_index", 0) or 0)
            end_idx = int(annotation.get("end_index", 0) or 0)
            snippet = block_text[start_idx:end_idx].strip() if end_idx > start_idx else ""
            citations_by_url[url] = {
                "title": str(annotation.get("title") or "").strip(),
                "snippet": snippet,
            }

    candidates = parsed_output.get("candidates", []) if parsed_output else []
    if not isinstance(candidates, list):
        candidates = []

    results: list[dict[str, Any]] = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        link = _normalize_url(str(item.get("url") or "").strip())
        if not link.startswith("http"):
            continue
        if _is_twitter_url(link):
            continue
        if link in seen:
            continue
        seen.add(link)
        citation = citations_by_url.get(link, {})
        results.append({
            "title": str(item.get("title") or "").strip() or citation.get("title") or link,
            "url": link,
            "snippet": str(item.get("snippet") or "").strip() or citation.get("snippet") or "",
        })
        if len(results) >= limit:
            break

    if not results and citations_by_url:
        for url, citation in list(citations_by_url.items())[:limit]:
            if _is_twitter_url(url):
                continue
            results.append({
                "title": citation.get("title") or urlparse(url).netloc or url,
                "url": url,
                "snippet": citation.get("snippet") or "",
            })

    note = None
    if executed_queries:
        note = f"gemini_queries={executed_queries}"
    if not results:
        suffix = "gemini_no_candidates"
        note = f"{note};{suffix}" if note else suffix
    return results, note



def _youtube_text(node: dict[str, Any] | None) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"]).strip()
    return "".join(str(run.get("text", "")) for run in node.get("runs") or []).strip()


def _parse_youtube_duration_seconds(raw: str | None) -> int | None:
    if not raw:
        return None
    parts = str(raw).strip().split(":")
    if not all(part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _parse_youtube_relative_date(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().lower()
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    amount = int(match.group(1))

    if any(unit in text for unit in ("second", "seconds", "秒")):
        delta = timedelta(seconds=amount)
    elif any(unit in text for unit in ("minute", "minutes", "分鐘", "分钟")):
        delta = timedelta(minutes=amount)
    elif any(unit in text for unit in ("hour", "hours", "小時", "小时")):
        delta = timedelta(hours=amount)
    elif any(unit in text for unit in ("day", "days", "日", "天")):
        delta = timedelta(days=amount)
    elif any(unit in text for unit in ("week", "weeks", "週", "周", "星期")):
        delta = timedelta(weeks=amount)
    elif any(unit in text for unit in ("month", "months", "個月", "个月", "月")):
        delta = timedelta(days=amount * 30)
    elif any(unit in text for unit in ("year", "years", "年")):
        delta = timedelta(days=amount * 365)
    else:
        return None

    return (datetime.now(timezone.utc) - delta).date().isoformat()


def _extract_youtube_initial_data(html: str) -> dict[str, Any] | None:
    patterns = [
        r"var ytInitialData = (\{.*?\});</script>",
        r"ytInitialData\"\] = (\{.*?\});",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _iter_youtube_video_renderers(node: Any):
    if isinstance(node, dict):
        renderer = node.get("videoRenderer")
        if isinstance(renderer, dict):
            yield renderer
        for value in node.values():
            yield from _iter_youtube_video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_youtube_video_renderers(value)


def _published_sort_key(item: dict[str, Any]) -> datetime:
    published_at = item.get("published_at")
    if not published_at:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _parse_yt_dlp_upload_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    try:
        dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        return dt.date().isoformat()
    except Exception:
        return None


def _fetch_youtube_video_metadata(url: str) -> dict[str, Any]:
    """Fetch full YouTube metadata without reading transcripts or downloading media."""
    try:
        import yt_dlp
    except Exception as exc:
        logger.warning("[topic] yt-dlp unavailable for %s: %s", url, exc)
        return {}

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        opts["proxy"] = SOCKS_PROXY

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("[topic] youtube metadata fetch failed for %s: %s", url, exc)
        return {}

    published_at = (
        _parse_yt_dlp_upload_date(info.get("upload_date"))
        or _parse_yt_dlp_upload_date(info.get("release_date"))
        or _parse_yt_dlp_upload_date(info.get("timestamp"))
    )
    duration_seconds = info.get("duration")
    try:
        duration_seconds = int(duration_seconds) if duration_seconds is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    return {
        "title": info.get("title"),
        "description": info.get("description") or info.get("full_description"),
        "published_at": published_at,
        "duration_seconds": duration_seconds,
        "channel": info.get("channel") or info.get("uploader"),
    }


def _search_youtube_videos(
    query: str,
    limit: int = TOPIC_MAX_RESULTS_PER_QUERY,
) -> tuple[list[dict[str, Any]], str | None]:
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=CAI%253D"
    try:
        resp = requests.get(
            search_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=25,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[topic] youtube search failed for %s: %s", query, exc)
        return [], f"youtube_search_failed: {exc}"

    payload = _extract_youtube_initial_data(resp.text)
    if not payload:
        return [], "youtube_initial_data_missing"

    results: list[dict[str, Any]] = []
    seen = set()
    for entry in _iter_youtube_video_renderers(payload):
        video_id = str(entry.get("videoId") or "").strip()
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)

        title = _youtube_text(entry.get("title"))
        if not title:
            continue

        snippets = entry.get("detailedMetadataSnippets") or []
        description = _youtube_text((snippets[0] if snippets else {}).get("snippetText"))
        length_text = _youtube_text(entry.get("lengthText"))
        published_text = _youtube_text(entry.get("publishedTimeText"))
        url = f"https://www.youtube.com/watch?v={video_id}"

        results.append({
            "title": title,
            "url": url,
            "snippet": description,
            "published_at": _parse_youtube_relative_date(published_text),
            "published_text": published_text,
            "duration_seconds": _parse_youtube_duration_seconds(length_text),
            "channel": _youtube_text(entry.get("ownerText")),
            "video_id": video_id,
        })

    results.sort(key=_published_sort_key, reverse=True)
    results = results[:limit]

    note = f"youtube_search_url={search_url};sort=upload_date_desc;raw_candidates={len(seen)}"
    if not results:
        note = f"{note};youtube_no_candidates"
    return results, note

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


def _canonical_key(
    topic: dict,
    title: str,
    published_at: str | None,
    url: str | None = None,
    platform: str | None = None,
) -> str:
    if platform == "youtube" and url:
        return hashlib.sha1(f"{topic['id']}|youtube|{_normalize_url(url)}".encode("utf-8")).hexdigest()

    date_key = (published_at or "")[:10]
    body = " ".join(_slug_tokens(title, topic)) or re.sub(r"\s+", " ", title.lower()).strip()[:80]
    return hashlib.sha1(f"{topic['id']}|{date_key}|{body}".encode("utf-8")).hexdigest()


def _candidate_url_already_ingested(urls: set[str]) -> bool:
    clean_urls = sorted({u for u in urls if u})
    if not clean_urls:
        return False
    placeholders = ",".join("?" * len(clean_urls))
    queries = (
        f"SELECT 1 FROM topic_items WHERE url IN ({placeholders}) LIMIT 1",
        f"""SELECT 1
            FROM topic_item_sources src
            JOIN topic_items item ON item.id = src.topic_item_id
            WHERE src.url IN ({placeholders})
            LIMIT 1""",
    )
    with db.get_conn() as conn:
        return any(conn.execute(query, clean_urls).fetchone() for query in queries)


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
            if "youtube.com" in parsed.netloc or _is_twitter_url(url):
                continue
            item["platform"] = "web"
            item["source_label"] = parsed.netloc
            results.append(item)
        if idx < len(capped_queries) - 1 and TOPIC_QUERY_SLEEP_SECONDS > 0:
            time.sleep(TOPIC_QUERY_SLEEP_SECONDS)
    return results


def _search_youtube(topic: dict, queries: list[str]) -> list[dict[str, Any]]:
    if "youtube" not in (topic.get("channels") or TOPIC_DEFAULT_CHANNELS):
        return []

    results: list[dict[str, Any]] = []
    seen = set()
    capped_queries = queries[:3]
    for idx, query in enumerate(capped_queries):
        query_results, note = _search_youtube_videos(query)
        _log_query_results(topic, "youtube", query, query_results, note=note)
        for item in query_results:
            url = item["url"]
            if url in seen:
                continue
            seen.add(url)
            item["platform"] = "youtube"
            item["source_label"] = item.get("channel") or "youtube.com"
            results.append(item)
        if idx < len(capped_queries) - 1 and TOPIC_QUERY_SLEEP_SECONDS > 0:
            time.sleep(TOPIC_QUERY_SLEEP_SECONDS)
    results.sort(key=_published_sort_key, reverse=True)
    return results


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
        raw_url = cand["url"]
        url = _normalize_url(raw_url)
        candidate_urls = {raw_url, url}
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if _candidate_url_already_ingested(candidate_urls):
            logger.info("[topic] skipped already ingested URL before AI: %s", url)
            continue

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
            duration_seconds = cand.get("duration_seconds")
            try:
                duration_seconds = int(duration_seconds) if duration_seconds is not None else None
            except (TypeError, ValueError):
                duration_seconds = None
            if duration_seconds is not None and duration_seconds < 20 * 60:
                logger.info("[topic] skipped short youtube video %s (%ss)", title[:120], duration_seconds)
                continue

            metadata = _fetch_youtube_video_metadata(primary_url)
            if metadata.get("title"):
                title = metadata["title"]
                cand["title"] = title
            if metadata.get("published_at"):
                published_at = metadata["published_at"]
            if metadata.get("channel"):
                cand["source_label"] = metadata["channel"]
            if metadata.get("duration_seconds") is not None:
                duration_seconds = metadata["duration_seconds"]
                cand["duration_seconds"] = duration_seconds
                if duration_seconds < 20 * 60:
                    logger.info("[topic] skipped short youtube video %s (%ss)", title[:120], duration_seconds)
                    continue

            description = metadata.get("description") or cand.get("snippet") or ""
            cand["snippet"] = description
            content = "\n\n".join(part for part in (title, description) if part)
        elif platform == "x":
            content = cand.get("snippet") or title

        if not _date_in_range(published_at, date_from, date_to):
            continue

        keep, confidence, reason = _classify_with_openai(topic, title, content or "", platform)
        if not keep or confidence < TOPIC_MIN_CONFIDENCE:
            logger.info("[topic] filtered out %s (%s)", title[:120], reason)
            continue

        enriched.append({
            "canonical_key": _canonical_key(topic, title, published_at, primary_url, platform),
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
                "duration_seconds": cand.get("duration_seconds"),
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
        persisted.append({
            "id": item_id,
            "new": is_new,
            "title": cand["title"],
            "url": cand["url"],
            "published_at": cand.get("published_at"),
            "duration_seconds": cand.get("supporting_sources", [{}])[0].get("duration_seconds"),
            "primary_platform": cand["primary_platform"],
            "confidence": cand["confidence"],
        })

    db.update_topic_last_fetched(topic["id"])
    return persisted


def _run_topic_pipeline(topic: dict, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    queries = _build_queries(topic)
    candidates = [
        *_search_web(topic, queries),
        *_search_youtube(topic, queries),
        *_search_x(topic, queries),
    ]
    enriched = _enrich_candidates(topic, candidates, date_from, date_to)
    persisted = _dedupe_and_persist(topic, enriched)
    return {
        "candidate_count": len(candidates),
        "relevant_count": len(persisted),
        "items": persisted,
    }


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
        pipeline_result = _run_topic_pipeline(topic, date_from, date_to)
        items = pipeline_result["items"]
        new_count = sum(1 for item in items if item["new"])
        total_new += new_count
        results.append({
            "name": topic["name"],
            "type": "topic",
            "new": new_count,
            "fetched": len(items),
            "candidate_count": pipeline_result["candidate_count"],
            "relevant_count": pipeline_result["relevant_count"],
            "items": items,
            "error": None,
        })

    return {"total_new": total_new, "sources": results}
