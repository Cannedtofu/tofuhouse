"""GPU price history — time-series storage, one row per GPU+day.

Historical points are never deleted — INSERT OR IGNORE means each daily fetch
only adds new points, preserving data older than the API's rolling window.
"""

from datetime import datetime, timezone

from db.core import get_conn


def upsert_gpu_price_data(gpu_type: str, data: list) -> None:
    """Merge new price points into gpu_price_history.

    Uses INSERT OR IGNORE so existing (gpu_type, timestamp) rows are untouched —
    historical data is preserved even if it falls outside the API's return window.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO gpu_price_history
                   (gpu_type, timestamp, index_value, fetched_at)
               VALUES (?, ?, ?, ?)""",
            [(gpu_type, pt["timestamp"], pt["index_value"], now) for pt in data],
        )


def get_gpu_price_data(gpu_type: str) -> dict | None:
    """Return full price history for a single GPU type, ordered by timestamp."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT timestamp, index_value, MAX(fetched_at) AS fetched_at
               FROM gpu_price_history
               WHERE gpu_type = ?
               GROUP BY timestamp
               ORDER BY timestamp""",
            (gpu_type,),
        ).fetchall()
        if not rows:
            return None
        return {
            "gpu_type": gpu_type,
            "data": [{"timestamp": r["timestamp"], "index_value": r["index_value"]} for r in rows],
            "fetched_at": rows[-1]["fetched_at"],
        }


def get_all_gpu_price_data() -> list:
    """Return full price history for all GPU types.

    Returns a list of dicts: [{gpu_type, data: [{timestamp, index_value}], fetched_at}]
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT gpu_type, timestamp, index_value
               FROM gpu_price_history
               ORDER BY gpu_type, timestamp"""
        ).fetchall()

    # Group into per-GPU dicts
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["gpu_type"], []).append(
            {"timestamp": r["timestamp"], "index_value": r["index_value"]}
        )

    return [
        {"gpu_type": gpu_type, "data": points, "fetched_at": None}
        for gpu_type, points in sorted(grouped.items())
    ]


def get_gpu_price_last_updated() -> str | None:
    """Return the most recent fetched_at timestamp across all GPU types."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS last FROM gpu_price_history"
        ).fetchone()
        return row["last"] if row else None
