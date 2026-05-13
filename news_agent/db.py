"""SQLite database layer for news_agent."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
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


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                type         TEXT    NOT NULL CHECK(type IN ('rss','nitter','web')),
                url          TEXT    NOT NULL UNIQUE,
                url_filter   TEXT,
                last_fetched TEXT
            );

            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                title        TEXT,
                url          TEXT    NOT NULL UNIQUE,
                content      TEXT,
                published_at TEXT,
                fetched_at   TEXT    NOT NULL,
                summary      TEXT
            );

            CREATE TABLE IF NOT EXISTS fetch_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT    NOT NULL,
                finished_at  TEXT,
                trigger      TEXT    NOT NULL DEFAULT 'manual',
                total_new    INTEGER NOT NULL DEFAULT 0,
                total_fetched INTEGER NOT NULL DEFAULT 0,
                sources_json TEXT,
                error        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_articles_source   ON articles(source_id);
            CREATE INDEX IF NOT EXISTS idx_articles_pub_date ON articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_fetch_log_started ON fetch_log(started_at);
        """)
        # Migrations for older databases
        try:
            conn.execute("ALTER TABLE sources ADD COLUMN url_filter TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE fetch_log ADD COLUMN total_fetched INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass


def seed_default_sources():
    """Insert DEFAULT_SOURCES into the DB if no sources exist yet."""
    from config import DEFAULT_SOURCES
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        if count > 0:
            return
    for s in DEFAULT_SOURCES:
        upsert_source(
            name=s["name"],
            type_=s["type"],
            url=s["url"],
            url_filter=s.get("url_filter"),
        )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def get_all_sources() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sources ORDER BY name"
        ).fetchall()


def get_source_by_id(source_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()


def upsert_source(name: str, type_: str, url: str, url_filter: Optional[str] = None) -> int:
    """Insert or update a source; returns its id."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sources (name, type, url, url_filter)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   name=excluded.name,
                   type=excluded.type,
                   url_filter=excluded.url_filter""",
            (name, type_, url, url_filter),
        )
        row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
        return row["id"]


def delete_source(source_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


def update_source_last_fetched(source_id: int):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_fetched = ? WHERE id = ?", (now, source_id)
        )


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def insert_article(
    source_id: int,
    title: str,
    url: str,
    content: str,
    published_at: Optional[str],
) -> bool:
    """Insert article; returns True if newly inserted, False if already existed.
    If the article already exists and the new content is longer, the content is updated.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, content FROM articles WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            # Update content if the new version is richer than what's stored
            if content and len(content) > len(existing["content"] or ""):
                conn.execute(
                    "UPDATE articles SET content = ? WHERE id = ?",
                    (content, existing["id"]),
                )
            return False
        conn.execute(
            """INSERT INTO articles
               (source_id, title, url, content, published_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, title, url, content, published_at, fetched_at),
        )
        return True


def get_article_by_id(article_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT a.*, s.name AS source_name, s.type AS source_type
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.id = ?""",
            (article_id,),
        ).fetchone()


def get_unsummarized_articles() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM articles WHERE summary IS NULL ORDER BY fetched_at"
        ).fetchall()


def update_summary(article_id: int, summary: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET summary = ? WHERE id = ?", (summary, article_id)
        )


def get_articles(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_ids: Optional[list[int]] = None,
) -> list[sqlite3.Row]:
    """
    Query articles with optional filters.
    date_from / date_to: ISO date strings like '2026-04-10'
    source_ids: list of source IDs to include; None means all
    """
    query = """
        SELECT a.*, s.name AS source_name, s.type AS source_type
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE 1=1
    """
    params: list = []

    if date_from:
        query += " AND (a.published_at >= ? OR (a.published_at IS NULL AND a.fetched_at >= ?))"
        params += [date_from, date_from]
    if date_to:
        # include the full end day
        date_to_end = date_to + "T23:59:59"
        query += " AND (a.published_at <= ? OR (a.published_at IS NULL AND a.fetched_at <= ?))"
        params += [date_to_end, date_to_end]
    if source_ids:
        placeholders = ",".join("?" * len(source_ids))
        query += f" AND a.source_id IN ({placeholders})"
        params += source_ids

    query += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC"

    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


# ---------------------------------------------------------------------------
# Fetch log
# ---------------------------------------------------------------------------

def log_fetch_start(trigger: str = "manual") -> int:
    """Record the start of a fetch run; returns the log entry id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fetch_log (started_at, trigger) VALUES (?, ?)",
            (now, trigger),
        )
        return cur.lastrowid


def log_fetch_finish(log_id: int, result: dict, error: Optional[str] = None):
    """Update a fetch log entry with the final result."""
    now = datetime.now(timezone.utc).isoformat()
    sources_json = json.dumps(result.get("sources", []))
    total_new = result.get("total_new", 0)
    total_fetched = sum(s.get("fetched", 0) for s in result.get("sources", []))
    with get_conn() as conn:
        conn.execute(
            """UPDATE fetch_log
               SET finished_at=?, total_new=?, total_fetched=?, sources_json=?, error=?
               WHERE id=?""",
            (now, total_new, total_fetched, sources_json, error, log_id),
        )


def get_fetch_log(limit: int = 50) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM fetch_log ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
