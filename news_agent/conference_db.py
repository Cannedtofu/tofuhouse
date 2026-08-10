"""Separate SQLite store for roadshow conference calls."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import CONFERENCE_DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(CONFERENCE_DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conference_calls (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                url           TEXT NOT NULL UNIQUE,
                starts_at     TEXT,
                date_text     TEXT,
                raw_text      TEXT,
                fetched_at    TEXT NOT NULL,
                last_seen_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conference_calls_starts
                ON conference_calls(starts_at);

            CREATE TABLE IF NOT EXISTS user_conference_topics (
                user_id     INTEGER PRIMARY KEY,
                topics_json TEXT NOT NULL DEFAULT '[]',
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conference_topics (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_by_user_id INTEGER,
                created_at         TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conference_topics_name
                ON conference_topics(name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS user_conference_topic_follows (
                user_id  INTEGER NOT NULL,
                topic_id INTEGER NOT NULL REFERENCES conference_topics(id) ON DELETE CASCADE,
                followed_at TEXT NOT NULL,
                PRIMARY KEY (user_id, topic_id)
            );
            CREATE INDEX IF NOT EXISTS idx_conference_topic_follows_user
                ON user_conference_topic_follows(user_id);

            CREATE TABLE IF NOT EXISTS conference_matches (
                user_id       INTEGER NOT NULL,
                conference_id INTEGER NOT NULL REFERENCES conference_calls(id) ON DELETE CASCADE,
                topic         TEXT NOT NULL,
                matched       INTEGER NOT NULL DEFAULT 0,
                reason        TEXT,
                matched_at    TEXT NOT NULL,
                PRIMARY KEY (user_id, conference_id, topic)
            );
            CREATE INDEX IF NOT EXISTS idx_conference_matches_user_topic
                ON conference_matches(user_id, topic, matched);
        """)
        _migrate_legacy_topics(conn)


def _migrate_legacy_topics(conn):
    rows = conn.execute("SELECT user_id, topics_json FROM user_conference_topics").fetchall()
    now = _now_iso()
    for row in rows:
        try:
            topics = json.loads(row["topics_json"] or "[]")
        except Exception:
            topics = []
        for name in topics:
            name = str(name or "").strip()
            if not name:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO conference_topics (name, created_by_user_id, created_at)
                   VALUES (?, ?, ?)""",
                (name, row["user_id"], now),
            )
            topic_row = conn.execute(
                "SELECT id FROM conference_topics WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if topic_row:
                conn.execute(
                    """INSERT OR IGNORE INTO user_conference_topic_follows
                       (user_id, topic_id, followed_at) VALUES (?, ?, ?)""",
                    (row["user_id"], topic_row["id"], now),
                )


def upsert_conferences(items):
    now = _now_iso()
    inserted = 0
    updated = 0
    with get_conn() as conn:
        for item in items:
            existing = conn.execute(
                "SELECT id FROM conference_calls WHERE url = ?",
                (item["url"],),
            ).fetchone()
            if existing:
                updated += 1
                conn.execute(
                    """UPDATE conference_calls
                       SET title = ?, starts_at = ?, date_text = ?, raw_text = ?, last_seen_at = ?
                       WHERE url = ?""",
                    (
                        item["title"],
                        item.get("starts_at"),
                        item.get("date_text"),
                        item.get("raw_text"),
                        now,
                        item["url"],
                    ),
                )
            else:
                inserted += 1
                conn.execute(
                    """INSERT INTO conference_calls
                       (title, url, starts_at, date_text, raw_text, fetched_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["title"],
                        item["url"],
                        item.get("starts_at"),
                        item.get("date_text"),
                        item.get("raw_text"),
                        now,
                        now,
                    ),
                )
    return {"inserted": inserted, "updated": updated}


def create_topic(name, user_id=None, follow=True):
    name = (name or "").strip()
    if not name:
        raise ValueError("Topic name is required")
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO conference_topics (name, created_by_user_id, created_at)
               VALUES (?, ?, ?)""",
            (name, user_id, now),
        )
        row = conn.execute(
            "SELECT * FROM conference_topics WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if follow and user_id is not None:
            conn.execute(
                """INSERT OR IGNORE INTO user_conference_topic_follows
                   (user_id, topic_id, followed_at) VALUES (?, ?, ?)""",
                (user_id, row["id"], now),
            )
    return dict(row)


def get_topics(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.name
               FROM conference_topics t
               JOIN user_conference_topic_follows f ON f.topic_id = t.id
               WHERE f.user_id = ?
               ORDER BY t.name COLLATE NOCASE""",
            (user_id,),
        ).fetchall()
    return [row["name"] for row in rows]


def get_all_topics_with_follow_status(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*,
                      CASE WHEN f.topic_id IS NOT NULL THEN 1 ELSE 0 END AS followed
               FROM conference_topics t
               LEFT JOIN user_conference_topic_follows f
                 ON f.topic_id = t.id AND f.user_id = ?
               ORDER BY t.name COLLATE NOCASE""",
            (user_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["followed"] = bool(row["followed"])
        result.append(item)
    return result


def follow_topic(user_id, topic_id):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO user_conference_topic_follows
               (user_id, topic_id, followed_at) VALUES (?, ?, ?)""",
            (user_id, int(topic_id), _now_iso()),
        )


def unfollow_topic(user_id, topic_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_conference_topic_follows WHERE user_id = ? AND topic_id = ?",
            (user_id, int(topic_id)),
        )


def delete_topic(topic_id):
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM conference_topics WHERE id = ?", (int(topic_id),)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM user_conference_topic_follows WHERE topic_id = ?", (int(topic_id),))
        conn.execute("DELETE FROM conference_matches WHERE topic = ?", (row["name"],))
        conn.execute("DELETE FROM conference_topics WHERE id = ?", (int(topic_id),))


def set_topics(user_id, topics):
    clean = []
    seen = set()
    for topic in topics:
        topic = topic.strip()
        key = topic.casefold()
        if topic and key not in seen:
            clean.append(topic)
            seen.add(key)
    with get_conn() as conn:
        conn.execute("DELETE FROM user_conference_topic_follows WHERE user_id = ?", (user_id,))
    for topic in clean:
        create_topic(topic, user_id=user_id, follow=True)
    return clean


def list_future_conferences(days=5):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT *
               FROM conference_calls
               WHERE starts_at IS NULL OR date(starts_at) BETWEEN date('now', 'localtime') AND date('now', 'localtime', ?)
               ORDER BY starts_at IS NULL, starts_at, id""",
            (f"+{int(days)} days",),
        ).fetchall()
    return [dict(row) for row in rows]


def list_conferences_missing_matches(user_id, topics, days=5):
    topics = [topic.strip() for topic in topics if topic.strip()]
    if not topics:
        return []
    conferences = list_future_conferences(days=days)
    missing = []
    with get_conn() as conn:
        for conference in conferences:
            rows = conn.execute(
                """SELECT topic FROM conference_matches
                   WHERE user_id = ? AND conference_id = ?""",
                (user_id, conference["id"]),
            ).fetchall()
            existing_topics = {row["topic"] for row in rows}
            missing_topics = [topic for topic in topics if topic not in existing_topics]
            if missing_topics:
                item = dict(conference)
                item["topics_to_match"] = missing_topics
                missing.append(item)
    return missing


def replace_matches(user_id, conference_ids, matches_by_conference):
    now = _now_iso()
    ids = [int(cid) for cid in conference_ids]
    with get_conn() as conn:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM conference_matches WHERE user_id = ? AND conference_id IN ({placeholders})",
                [user_id, *ids],
            )
        _write_matches(conn, user_id, matches_by_conference, now)


def upsert_matches(user_id, matches_by_conference):
    with get_conn() as conn:
        _write_matches(conn, user_id, matches_by_conference, _now_iso())


def _write_matches(conn, user_id, matches_by_conference, matched_at):
    for conference_id, matches in matches_by_conference.items():
        for match in matches:
            conn.execute(
                """INSERT OR REPLACE INTO conference_matches
                   (user_id, conference_id, topic, matched, reason, matched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    int(conference_id),
                    match["topic"],
                    1 if match.get("matched") else 0,
                    match.get("reason"),
                    matched_at,
                ),
            )


def get_grouped_matches(user_id):
    topics = get_topics(user_id)
    if not topics:
        return []
    placeholders = ",".join("?" for _ in topics)
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT m.topic, m.reason, c.*
                FROM conference_matches m
                JOIN conference_calls c ON c.id = m.conference_id
                WHERE m.user_id = ? AND m.matched = 1 AND m.topic IN ({placeholders})
                ORDER BY m.topic COLLATE NOCASE, c.starts_at IS NULL, c.starts_at, c.id""",
            [user_id, *topics],
        ).fetchall()
    grouped = []
    index = {}
    for row in rows:
        topic = row["topic"]
        if topic not in index:
            index[topic] = {"topic": topic, "items": []}
            grouped.append(index[topic])
        item = dict(row)
        item.pop("topic", None)
        index[topic]["items"].append(item)
    return grouped