"""Database operations for fetch run logging."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


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


def close_open_fetch_logs():
    """Mark any fetch_log entries still open (no finished_at) as interrupted.
    Called on startup to clean up runs that were killed mid-flight."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE fetch_log
               SET finished_at=?, error='interrupted (process restarted)'
               WHERE finished_at IS NULL""",
            (now,),
        )


def get_fetch_log(limit: int = 50) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM fetch_log ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
