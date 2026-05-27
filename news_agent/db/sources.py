"""Database operations for news sources."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


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
