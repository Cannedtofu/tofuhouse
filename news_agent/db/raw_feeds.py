"""Database operations for raw feed digest subscriptions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from db.core import get_conn


DEFAULT_RAW_FEED_FREQUENCY_DAYS = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_topic_ids(value: str | None) -> list[int]:
    try:
        return [int(v) for v in json.loads(value or "[]")]
    except Exception:
        return []


def _row_to_subscription(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "topic_ids": _parse_topic_ids(row["topic_ids_json"]),
        "enabled": bool(row["enabled"]),
        "frequency_days": int(row["frequency_days"] or DEFAULT_RAW_FEED_FREQUENCY_DAYS),
        "last_sent": row["last_sent"],
        "created_at": row["created_at"],
    }


def _default_topic_ids_for_user(conn: sqlite3.Connection, user_id: int) -> list[int]:
    rows = conn.execute(
        """SELECT t.id
           FROM topics t
           JOIN user_topic_follows f ON f.topic_id = t.id AND f.user_id = ?
           WHERE t.active = 1
           ORDER BY t.name""",
        (user_id,),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def get_raw_feed_subscription(user_id: int) -> dict:
    """Return the user's single raw-feed subscription, creating a disabled default if missing."""
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM raw_feed_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            topic_ids = _default_topic_ids_for_user(conn, user_id)
            conn.execute(
                """INSERT INTO raw_feed_subscriptions
                   (user_id, topic_ids_json, enabled, frequency_days, created_at)
                   VALUES (?, ?, 0, ?, ?)""",
                (
                    user_id,
                    json.dumps(topic_ids, ensure_ascii=False),
                    DEFAULT_RAW_FEED_FREQUENCY_DAYS,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM raw_feed_subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
    return _row_to_subscription(row)


def update_raw_feed_subscription(
    user_id: int,
    topic_ids: list[int],
    enabled: bool,
    frequency_days: int,
) -> dict:
    if frequency_days not in (1, 3, 7, 14):
        frequency_days = DEFAULT_RAW_FEED_FREQUENCY_DAYS
    topic_ids = sorted({int(tid) for tid in topic_ids})
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO raw_feed_subscriptions
               (user_id, topic_ids_json, enabled, frequency_days, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   topic_ids_json = excluded.topic_ids_json,
                   enabled = excluded.enabled,
                   frequency_days = excluded.frequency_days""",
            (
                user_id,
                json.dumps(topic_ids, ensure_ascii=False),
                1 if enabled else 0,
                frequency_days,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM raw_feed_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_subscription(row)



def get_raw_feed_subscriptions_for_users(user_ids: list[int]) -> list[dict]:
    """Return existing raw-feed subscriptions for admin views, with user emails."""
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT rfs.*, u.email AS user_email
                FROM raw_feed_subscriptions rfs
                JOIN users u ON u.id = rfs.user_id
                WHERE rfs.user_id IN ({placeholders})""",
            user_ids,
        ).fetchall()
    result = []
    for row in rows:
        sub = _row_to_subscription(row)
        sub["user_email"] = row["user_email"]
        result.append(sub)
    return result

def get_raw_feed_subscriptions_due() -> list[dict]:
    """Return enabled raw-feed subscriptions whose next send date has arrived."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rfs.*, u.email AS user_email
               FROM raw_feed_subscriptions rfs
               JOIN users u ON u.id = rfs.user_id
               WHERE rfs.enabled = 1
                 AND (rfs.last_sent IS NULL
                      OR date(rfs.last_sent, '+' || rfs.frequency_days || ' days') <= date('now'))"""
        ).fetchall()
    result = []
    for row in rows:
        sub = _row_to_subscription(row)
        sub["user_email"] = row["user_email"]
        result.append(sub)
    return result


def update_raw_feed_subscription_last_sent(subscription_id: int) -> None:
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE raw_feed_subscriptions SET last_sent = ? WHERE id = ?",
            (now, subscription_id),
        )
