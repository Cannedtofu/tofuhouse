"""Database operations for articles."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


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


def delete_article(article_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


def get_digest_abstract(article_id: int) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT digest_abstract FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return row["digest_abstract"] if row else None


def update_digest_abstract(article_id: int, abstract: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET digest_abstract = ? WHERE id = ?", (abstract, article_id)
        )


def update_article_translation(article_id: int, translated_content: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET translated_content = ? WHERE id = ?",
            (translated_content, article_id),
        )
