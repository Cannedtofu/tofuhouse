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


# ---------------------------------------------------------------------------
# Digest presets
# ---------------------------------------------------------------------------

MAX_PRESETS_PER_USER = 2

_PRESET_COLS = "id, name, source_ids_json, digest_enabled, digest_frequency_days, digest_last_sent"


def _row_to_preset(r) -> dict:
    return {
        "id":                   r["id"],
        "name":                 r["name"],
        "source_ids":           json.loads(r["source_ids_json"] or "[]"),
        "digest_enabled":       bool(r["digest_enabled"]),
        "digest_frequency_days": r["digest_frequency_days"],
        "digest_last_sent":     r["digest_last_sent"],
    }


def get_digest_presets_for_users(user_ids: list[int]) -> list[dict]:
    """Return all presets belonging to any of the given user IDs (batch lookup)."""
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT user_id, {_PRESET_COLS} FROM digest_presets WHERE user_id IN ({placeholders})",
            user_ids,
        ).fetchall()
    return [{"user_id": r["user_id"], **_row_to_preset(r)} for r in rows]


def get_digest_presets(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_PRESET_COLS} FROM digest_presets WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
    return [_row_to_preset(r) for r in rows]


def get_digest_preset(preset_id: int, user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_PRESET_COLS} FROM digest_presets WHERE id = ? AND user_id = ?",
            (preset_id, user_id),
        ).fetchone()
    return _row_to_preset(row) if row else None

def get_digest_preset_for_admin(preset_id: int) -> Optional[dict]:
    """Return a preset with its owner email for admin-triggered operations."""
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT dp.user_id, u.email AS user_email, {_PRESET_COLS}
                FROM digest_presets dp
                JOIN users u ON u.id = dp.user_id
                WHERE dp.id = ?""",
            (preset_id,),
        ).fetchone()
    if not row:
        return None
    return {"user_id": row["user_id"], "user_email": row["user_email"], **_row_to_preset(row)}


def create_digest_preset(user_id: int, name: str, source_ids: list[int]) -> Optional[dict]:
    """Returns the new preset dict, or None if the user already has MAX_PRESETS_PER_USER."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM digest_presets WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count >= MAX_PRESETS_PER_USER:
            return None
        cur = conn.execute(
            "INSERT INTO digest_presets (user_id, name, source_ids_json, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, json.dumps(source_ids), now),
        )
        return {"id": cur.lastrowid, "name": name, "source_ids": source_ids,
                "digest_enabled": False, "digest_frequency_days": 7, "digest_last_sent": None}


def update_digest_preset(
    preset_id: int,
    user_id: int,
    name: str,
    source_ids: list[int],
    digest_enabled: int = 0,
    digest_frequency_days: int = 7,
):
    with get_conn() as conn:
        conn.execute(
            """UPDATE digest_presets
               SET name = ?, source_ids_json = ?, digest_enabled = ?, digest_frequency_days = ?
               WHERE id = ? AND user_id = ?""",
            (name, json.dumps(source_ids), digest_enabled, digest_frequency_days, preset_id, user_id),
        )


def update_preset_email_settings(preset_id: int, enabled: bool, frequency_days: int):
    """Update only the email schedule fields on a preset (admin use, no user_id guard)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE digest_presets SET digest_enabled = ?, digest_frequency_days = ? WHERE id = ?",
            (1 if enabled else 0, frequency_days, preset_id),
        )


def update_preset_source_ids(preset_id: int, source_ids: list[int]):
    """Replace the source list on a preset without touching other fields."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE digest_presets SET source_ids_json = ? WHERE id = ?",
            (json.dumps(source_ids), preset_id),
        )


def delete_digest_preset(preset_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM digest_presets WHERE id = ? AND user_id = ?",
            (preset_id, user_id),
        )


def get_presets_due_for_email() -> list[dict]:
    """Return enabled presets whose next send date has arrived, with the user's email included."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT dp.id, dp.user_id, dp.name, dp.source_ids_json, dp.digest_frequency_days,
                      u.email AS user_email
               FROM digest_presets dp
               JOIN users u ON u.id = dp.user_id
               WHERE dp.digest_enabled = 1
               AND (dp.digest_last_sent IS NULL
                    OR date(dp.digest_last_sent, '+' || dp.digest_frequency_days || ' days') <= date('now'))"""
        ).fetchall()
    return [
        {
            "id":                    r["id"],
            "user_id":               r["user_id"],
            "name":                  r["name"],
            "source_ids":            json.loads(r["source_ids_json"] or "[]"),
            "digest_frequency_days": r["digest_frequency_days"],
            "user_email":            r["user_email"],
        }
        for r in rows
    ]


def update_preset_last_sent(preset_id: int):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE digest_presets SET digest_last_sent = ? WHERE id = ?", (now, preset_id)
        )


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
