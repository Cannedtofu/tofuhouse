"""GPU price cache — stores latest index history from api.ornnai.com."""

import json
from datetime import datetime, timezone

from db.core import get_conn


def upsert_gpu_price_data(gpu_type: str, data: list) -> None:
    """Insert or replace the full price history for a GPU type."""
    now = datetime.now(timezone.utc).isoformat()
    data_json = json.dumps(data)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO gpu_price_cache (gpu_type, data_json, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(gpu_type) DO UPDATE SET
                   data_json  = excluded.data_json,
                   fetched_at = excluded.fetched_at""",
            (gpu_type, data_json, now),
        )


def get_gpu_price_data(gpu_type: str) -> dict | None:
    """Return cached data for a single GPU type, or None if not fetched yet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT gpu_type, data_json, fetched_at FROM gpu_price_cache WHERE gpu_type = ?",
            (gpu_type,),
        ).fetchone()
        if row is None:
            return None
        return {
            "gpu_type": row["gpu_type"],
            "data": json.loads(row["data_json"]),
            "fetched_at": row["fetched_at"],
        }


def get_all_gpu_price_data() -> list:
    """Return cached data for all GPU types, ordered by gpu_type."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT gpu_type, data_json, fetched_at FROM gpu_price_cache ORDER BY gpu_type"
        ).fetchall()
        return [
            {
                "gpu_type": r["gpu_type"],
                "data": json.loads(r["data_json"]),
                "fetched_at": r["fetched_at"],
            }
            for r in rows
        ]


def get_gpu_price_last_updated() -> str | None:
    """Return the most recent fetched_at timestamp across all GPU types."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS last FROM gpu_price_cache"
        ).fetchone()
        return row["last"] if row else None
