"""Database operations for AI digest cache and token usage tracking."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


def get_all_digests_with_meta(limit: int = 100) -> list[dict]:
    """Return all digests with date range and source names derived from their article IDs."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, article_ids_json, content, created_at FROM digests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for row in rows:
        article_ids = json.loads(row["article_ids_json"] or "[]")
        date_from = date_to = None
        sources: list[str] = []

        if article_ids:
            placeholders = ",".join("?" * len(article_ids))
            with get_conn() as conn:
                meta = conn.execute(
                    f"""SELECT MIN(COALESCE(a.published_at, a.fetched_at)) AS date_from,
                               MAX(COALESCE(a.published_at, a.fetched_at)) AS date_to,
                               GROUP_CONCAT(DISTINCT s.name) AS sources
                        FROM articles a
                        JOIN sources s ON s.id = a.source_id
                        WHERE a.id IN ({placeholders})""",
                    article_ids,
                ).fetchone()
            if meta:
                date_from = (meta["date_from"] or "")[:10] or None
                date_to   = (meta["date_to"]   or "")[:10] or None
                sources   = sorted(set(meta["sources"].split(",") if meta["sources"] else []))

        result.append({
            "id":            row["id"],
            "created_at":    row["created_at"],
            "content":       row["content"],
            "article_count": len(article_ids),
            "date_from":     date_from,
            "date_to":       date_to,
            "sources":       sources,
        })

    return result


def get_digest_cache(article_ids_hash: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM digests WHERE article_ids_hash = ?", (article_ids_hash,)
        ).fetchone()
        return row["content"] if row else None


def save_digest_cache(article_ids_hash: str, article_ids_json: str, content: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO digests (article_ids_hash, article_ids_json, content, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(article_ids_hash) DO UPDATE SET content=excluded.content, created_at=excluded.created_at""",
            (article_ids_hash, article_ids_json, content, now),
        )


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def log_token_usage(
    operation: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    user_id: Optional[int] = None,
):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage (user_id, operation, model, tokens_in, tokens_out, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, operation, model, tokens_in, tokens_out, now),
        )


def get_token_usage_summary(user_id: Optional[int] = None) -> list[sqlite3.Row]:
    """Totals grouped by operation + model. Pass user_id=None for global (browser_agent)."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """SELECT operation, model,
                          SUM(tokens_in) AS total_in, SUM(tokens_out) AS total_out,
                          COUNT(*) AS calls
                   FROM token_usage WHERE user_id = ?
                   GROUP BY operation, model ORDER BY operation""",
                (user_id,),
            ).fetchall()
        return conn.execute(
            """SELECT operation, model,
                      SUM(tokens_in) AS total_in, SUM(tokens_out) AS total_out,
                      COUNT(*) AS calls
               FROM token_usage
               GROUP BY operation, model ORDER BY operation""",
        ).fetchall()


def get_token_usage_by_user_week() -> list[sqlite3.Row]:
    """Token usage per user for the past 7 days, grouped by user email."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT COALESCE(u.email, '(system)') AS email,
                      SUM(t.tokens_in)  AS total_in,
                      SUM(t.tokens_out) AS total_out,
                      COUNT(*)          AS calls
               FROM token_usage t
               LEFT JOIN users u ON u.id = t.user_id
               WHERE t.created_at >= date('now', '-7 days')
               GROUP BY t.user_id
               ORDER BY (total_in + total_out) DESC"""
        ).fetchall()
