"""Generate the independent raw-feed daily digest for topics and selected sources."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Iterable

import db
from article_summarizer import _chat, _get_client
from config import QWEN_SUMMARY_MODEL, RAW_FEED_BATCH_SIZE

logger = logging.getLogger(__name__)

_SYSTEM_MESSAGE = (
    "You rewrite RSS, YouTube, and topic-tracking items into concise Simplified Chinese blurbs. "
    "Use only the supplied title and description/content. Do not infer transcripts or external facts."
)

_BATCH_PROMPT = """\
请基于以下信息流条目的标题和描述，为每个条目改写一个更简洁的中文介绍。

硬性要求：
- 只依据给定的 title 和 content，不读取逐字稿，不补充外部事实。
- intro 使用简体中文，150 个中文字符以内。
- 不要营销腔，不要说“这个视频/这篇文章/本视频/本文”。
- 严格输出 JSON 数组，不要 Markdown，不要额外解释。
- 数组每项格式：{{"id": "条目 id", "intro": "简介"}}

Items:
{items_json}
"""

_ALLOWED_SOURCE_TYPES = {"rss", "youtube"}


def _chunks(items: list[dict], size: int) -> Iterable[list[dict]]:
    size = max(1, int(size or 1))
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _clean_intro(text: str | None, fallback: str = "") -> str:
    clean = re.sub(r"\s+", " ", (text or fallback or "")).strip()
    if not clean:
        clean = "暂无简介。"
    return clean[:150]


def _fallback_intro(item: dict) -> str:
    return _clean_intro(item.get("content") or item.get("title") or "")


def _parse_json_array(text: str) -> list[dict]:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\[[\s\S]*\]", text or "")
        if not match:
            raise
        return json.loads(match.group(0))


def _topic_rows_to_items(rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        items.append({
            "id": f"topic:{row['id']}",
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "content": (row.get("content") or "")[:1200],
            "published_at": row.get("published_at") or row.get("fetched_at") or "",
            "group_name": row.get("topic_name") or "未命名话题",
            "group_type": "话题",
            "source_type": row.get("primary_platform") or "topic",
        })
    return items


def _article_rows_to_items(rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        source_type = (row.get("source_type") or "").lower()
        if source_type not in _ALLOWED_SOURCE_TYPES:
            continue
        items.append({
            "id": f"article:{row['id']}",
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "content": (row.get("summary") or row.get("content") or "")[:1200],
            "published_at": row.get("published_at") or row.get("fetched_at") or "",
            "group_name": row.get("source_name") or "未命名来源",
            "group_type": "YouTube" if source_type == "youtube" else "RSS",
            "source_type": source_type,
        })
    return items


def _dedupe_items(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        key = (item.get("url") or item["id"]).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _generate_intros(items: list[dict], user_id: int | None = None) -> dict[str, str]:
    if not items:
        return {}

    client = _get_client()
    result: dict[str, str] = {}
    usage: list[tuple[int, int]] = []

    for batch in _chunks(items, RAW_FEED_BATCH_SIZE):
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "content": item["content"],
                "published_at": item["published_at"],
                "group_name": item["group_name"],
                "group_type": item["group_type"],
                "source_type": item["source_type"],
            }
            for item in batch
        ]
        prompt = _BATCH_PROMPT.format(
            items_json=json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            raw = _chat(
                client,
                prompt,
                label=f"raw feed batch {batch[0]['id']}-{batch[-1]['id']}",
                max_tokens=900,
                system=_SYSTEM_MESSAGE,
                _acc=usage,
            )
            parsed = _parse_json_array(raw)
            by_id = {str(entry.get("id")): entry.get("intro") for entry in parsed if entry.get("id") is not None}
            for item in batch:
                result[item["id"]] = _clean_intro(by_id.get(item["id"]), _fallback_intro(item))
        except Exception as exc:
            logger.warning("Raw feed Qwen batch failed; using fallback intros: %s", exc)
            for item in batch:
                result[item["id"]] = _fallback_intro(item)

    if usage:
        total_in = sum(t[0] for t in usage)
        total_out = sum(t[1] for t in usage)
        db.log_token_usage("raw_feed_digest", QWEN_SUMMARY_MODEL, total_in, total_out, user_id=user_id)

    return result


def build_raw_feed_digest(
    topic_ids: list[int],
    date_from: str,
    date_to: str,
    user_id: int | None = None,
    source_ids: list[int] | None = None,
) -> str:
    """Build a markdown raw-feed digest for selected topics and RSS/YouTube sources."""
    items: list[dict] = []

    if topic_ids:
        topic_rows = [dict(r) for r in db.get_topic_feed_items(date_from=date_from, date_to=date_to, topic_ids=topic_ids)]
        items.extend(_topic_rows_to_items(topic_rows))

    if source_ids:
        article_rows = [dict(r) for r in db.get_articles(date_from=date_from, date_to=date_to, source_ids=source_ids)]
        items.extend(_article_rows_to_items(article_rows))

    items = _dedupe_items(sorted(items, key=lambda item: item.get("published_at") or "", reverse=True))
    if not items:
        return ""

    intros = _generate_intros(items, user_id=user_id)

    grouped: dict[str, list[dict]] = {}
    for item in items:
        group = f"{item['group_type']} · {item['group_name']}"
        grouped.setdefault(group, []).append(item)

    lines = ["# 新增信息流日报", "", f"*{date_from} to {date_to}，共 {len(items)} 条*", ""]
    for group_name, group_items in grouped.items():
        lines.append(f"## {group_name}")
        lines.append("")
        for item in group_items:
            title = item.get("title") or item.get("url") or "Untitled"
            url = item.get("url") or "#"
            pub = (item.get("published_at") or "")[:10]
            intro = intros.get(item["id"]) or _fallback_intro(item)
            prefix = f"{pub} · " if pub else ""
            lines.append(f"- {prefix}[{title}]({url})")
            lines.append(f"  {intro}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def date_range_for_frequency(frequency_days: int, date_to: date | None = None) -> tuple[str, str]:
    end = date_to or date.today()
    start = end - timedelta(days=max(1, int(frequency_days or 1)))
    return start.isoformat(), end.isoformat()