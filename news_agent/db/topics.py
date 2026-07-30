"""Database operations for tracked topics and topic items."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_topic(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "aliases": json.loads(row["aliases_json"] or "[]"),
        "channels": json.loads(row["channels_json"] or "[]"),
        "active": bool(row["active"]),
        "backfill_date_from": row["backfill_date_from"],
        "backfill_date_to": row["backfill_date_to"],
        "last_fetched": row["last_fetched"],
        "created_at": row["created_at"],
    }


def create_topic(
    name: str,
    aliases: list[str],
    channels: Optional[list[str]] = None,
    active: bool = True,
    backfill_date_from: Optional[str] = None,
    backfill_date_to: Optional[str] = None,
) -> dict:
    now = _now_iso()
    aliases = [a.strip() for a in aliases if a.strip()]
    channels = channels or ["youtube"]
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO topics
               (name, aliases_json, channels_json, active, backfill_date_from, backfill_date_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                name.strip(),
                json.dumps(aliases, ensure_ascii=False),
                json.dumps(channels),
                1 if active else 0,
                backfill_date_from,
                backfill_date_to,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM topics WHERE id = last_insert_rowid()").fetchone()
    return _row_to_topic(row)


def update_topic(
    topic_id: int,
    name: str,
    aliases: list[str],
    channels: list[str],
    active: bool,
    backfill_date_from: Optional[str] = None,
    backfill_date_to: Optional[str] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE topics
               SET name = ?, aliases_json = ?, channels_json = ?, active = ?,
                   backfill_date_from = ?, backfill_date_to = ?
               WHERE id = ?""",
            (
                name.strip(),
                json.dumps([a.strip() for a in aliases if a.strip()], ensure_ascii=False),
                json.dumps(channels),
                1 if active else 0,
                backfill_date_from,
                backfill_date_to,
                topic_id,
            ),
        )


def get_all_topics(active_only: bool = False) -> list[dict]:
    query = "SELECT * FROM topics"
    params: list = []
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_topic(r) for r in rows]


def get_topic_by_id(topic_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return _row_to_topic(row) if row else None


def delete_topic(topic_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))


def update_topic_last_fetched(topic_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE topics SET last_fetched = ? WHERE id = ?",
            (_now_iso(), topic_id),
        )


def get_followed_topic_ids(user_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT topic_id FROM user_topic_follows WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [r["topic_id"] for r in rows]


def follow_topic(user_id: int, topic_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_topic_follows (user_id, topic_id) VALUES (?, ?)",
            (user_id, topic_id),
        )


def unfollow_topic(user_id: int, topic_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_topic_follows WHERE user_id = ? AND topic_id = ?",
            (user_id, topic_id),
        )


def get_all_topics_with_follow_status(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*,
                      CASE WHEN f.topic_id IS NOT NULL THEN 1 ELSE 0 END AS followed
               FROM topics t
               LEFT JOIN user_topic_follows f
                 ON f.topic_id = t.id AND f.user_id = ?
               ORDER BY t.name""",
            (user_id,),
        ).fetchall()
    topics = []
    for row in rows:
        topic = _row_to_topic(row)
        topic["followed"] = bool(row["followed"])
        topics.append(topic)
    return topics


def get_topic_item_by_id(topic_item_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT ti.*, t.name AS topic_name
               FROM topic_items ti
               JOIN topics t ON t.id = ti.topic_id
               WHERE ti.id = ?""",
            (topic_item_id,),
        ).fetchone()


def delete_topic_item(topic_item_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM topic_items WHERE id = ?", (topic_item_id,))


def upsert_topic_item(
    topic_id: int,
    canonical_key: str,
    title: str,
    url: Optional[str],
    content: Optional[str],
    published_at: Optional[str],
    primary_platform: str,
    confidence: float = 0.0,
    supporting_sources: Optional[list[dict]] = None,
) -> tuple[int, bool]:
    supporting_sources = supporting_sources or []
    fetched_at = _now_iso()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM topic_items WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchone()
        is_new = existing is None
        if existing:
            item_id = existing["id"]
            best_content = content if len(content or "") > len(existing["content"] or "") else existing["content"]
            best_url = url or existing["url"]
            best_title = title or existing["title"]
            best_date = existing["published_at"] or published_at
            best_platform = primary_platform if len(content or "") >= len(existing["content"] or "") else existing["primary_platform"]
            best_conf = max(confidence, float(existing["confidence"] or 0))
            conn.execute(
                """UPDATE topic_items
                   SET title = ?, url = ?, content = ?, published_at = ?, fetched_at = ?,
                       primary_platform = ?, confidence = ?
                   WHERE id = ?""",
                (
                    best_title,
                    best_url,
                    best_content,
                    best_date,
                    fetched_at,
                    best_platform,
                    best_conf,
                    item_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO topic_items
                   (topic_id, canonical_key, title, url, content, published_at, fetched_at, primary_platform, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    topic_id,
                    canonical_key,
                    title,
                    url,
                    content,
                    published_at,
                    fetched_at,
                    primary_platform,
                    confidence,
                ),
            )
            item_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        for src in supporting_sources:
            conn.execute(
                """INSERT INTO topic_item_sources
                   (topic_item_id, platform, source_label, url, title, content_snippet, published_at, is_primary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                       topic_item_id = excluded.topic_item_id,
                       platform = excluded.platform,
                       source_label = excluded.source_label,
                       title = excluded.title,
                       content_snippet = excluded.content_snippet,
                       published_at = excluded.published_at,
                       is_primary = MAX(topic_item_sources.is_primary, excluded.is_primary)""",
                (
                    item_id,
                    src.get("platform") or primary_platform,
                    src.get("source_label"),
                    src["url"],
                    src.get("title"),
                    src.get("content_snippet"),
                    src.get("published_at"),
                    1 if src.get("is_primary") else 0,
                ),
            )
    return item_id, is_new


def get_topic_item_sources(topic_item_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT platform, source_label, url, title, content_snippet, published_at, is_primary
               FROM topic_item_sources
               WHERE topic_item_id = ?
               ORDER BY is_primary DESC, COALESCE(published_at, '') DESC, id""",
            (topic_item_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_topic_item_sources_bulk(topic_item_ids: list[int]) -> dict[int, list[dict]]:
    if not topic_item_ids:
        return {}
    placeholders = ",".join("?" * len(topic_item_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT topic_item_id, platform, source_label, url, title, content_snippet, published_at, is_primary
                FROM topic_item_sources
                WHERE topic_item_id IN ({placeholders})
                ORDER BY topic_item_id, is_primary DESC, COALESCE(published_at, '') DESC, id""",
            topic_item_ids,
        ).fetchall()
    result: dict[int, list[dict]] = {item_id: [] for item_id in topic_item_ids}
    for row in rows:
        result[row["topic_item_id"]].append({
            "platform": row["platform"],
            "source_label": row["source_label"],
            "url": row["url"],
            "title": row["title"],
            "content_snippet": row["content_snippet"],
            "published_at": row["published_at"],
            "is_primary": bool(row["is_primary"]),
        })
    return result


def get_topic_feed_items(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    topic_ids: Optional[list[int]] = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT ti.*, t.name AS topic_name
        FROM topic_items ti
        JOIN topics t ON t.id = ti.topic_id
        WHERE 1=1
    """
    params: list = []
    if date_from:
        query += " AND (ti.published_at >= ? OR (ti.published_at IS NULL AND ti.fetched_at >= ?))"
        params += [date_from, date_from]
    if date_to:
        date_to_end = date_to + "T23:59:59"
        query += " AND (ti.published_at <= ? OR (ti.published_at IS NULL AND ti.fetched_at <= ?))"
        params += [date_to_end, date_to_end]
    if topic_ids:
        placeholders = ",".join("?" * len(topic_ids))
        query += f" AND ti.topic_id IN ({placeholders})"
        params += topic_ids
    query += " ORDER BY COALESCE(ti.published_at, ti.fetched_at) DESC"
    with get_conn() as conn:
        return conn.execute(query, params).fetchall()
