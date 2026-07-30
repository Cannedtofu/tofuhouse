"""Generate the independent raw-feed daily digest for topic videos."""

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
    "You rewrite YouTube video descriptions into concise Simplified Chinese blurbs. "
    "Use only the supplied title and description. Do not infer transcript content or external facts."
)

_BATCH_PROMPT = """\
请基于以下 YouTube 视频的标题和描述，为每个视频改写一个更简洁的视频介绍。

硬性要求：
- 只依据给定的 title 和 content，不读取逐字稿，不补充外部事实。
- intro 使用简体中文，150 个中文字符以内。
- 不要营销腔，不要说“这个视频/本视频”。
- 严格输出 JSON 数组，不要 Markdown，不要额外解释。
- 数组每项格式：{{"id": 视频 id, "intro": "简介"}}

Videos:
{videos_json}
"""


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
    content = item.get("content") or ""
    title = item.get("title") or ""
    return _clean_intro(content or title)


def _parse_json_array(text: str) -> list[dict]:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\[[\s\S]*\]", text or "")
        if not match:
            raise
        return json.loads(match.group(0))


def _build_input_items(rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        items.append({
            "id": int(row["id"]),
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "content": (row.get("content") or "")[:1200],
            "published_at": row.get("published_at") or row.get("fetched_at") or "",
            "topic_name": row.get("topic_name") or "",
        })
    return items


def _generate_intros(items: list[dict], user_id: int | None = None) -> dict[int, str]:
    if not items:
        return {}

    client = _get_client()
    result: dict[int, str] = {}
    usage: list[tuple[int, int]] = []

    for batch in _chunks(items, RAW_FEED_BATCH_SIZE):
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "content": item["content"],
                "published_at": item["published_at"],
                "topic_name": item["topic_name"],
            }
            for item in batch
        ]
        prompt = _BATCH_PROMPT.format(
            videos_json=json.dumps(payload, ensure_ascii=False, indent=2)
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
            by_id = {int(entry.get("id")): entry.get("intro") for entry in parsed if entry.get("id") is not None}
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
) -> str:
    """Build a markdown raw-feed digest for topic videos in the given date range."""
    if not topic_ids:
        return ""

    rows = [dict(r) for r in db.get_topic_feed_items(date_from=date_from, date_to=date_to, topic_ids=topic_ids)]
    rows = [r for r in rows if (r.get("primary_platform") or "").lower() == "youtube"]
    if not rows:
        return ""

    input_items = _build_input_items(rows)
    intros = _generate_intros(input_items, user_id=user_id)

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("topic_name") or "未命名话题", []).append(row)

    lines = [f"# 新增信息流日报", "", f"*{date_from} to {date_to}，共 {len(rows)} 条视频*", ""]
    for topic_name, topic_rows in grouped.items():
        lines.append(f"## {topic_name}")
        lines.append("")
        for row in topic_rows:
            title = row.get("title") or row.get("url") or "Untitled video"
            url = row.get("url") or "#"
            pub = (row.get("published_at") or row.get("fetched_at") or "")[:10]
            intro = intros.get(int(row["id"])) or _fallback_intro(row)
            prefix = f"{pub} · " if pub else ""
            lines.append(f"- {prefix}[{title}]({url})")
            lines.append(f"  {intro}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def date_range_for_frequency(frequency_days: int, date_to: date | None = None) -> tuple[str, str]:
    end = date_to or date.today()
    start = end - timedelta(days=max(1, int(frequency_days or 1)))
    return start.isoformat(), end.isoformat()
