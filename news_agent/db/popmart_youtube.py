"""DB helpers for the POP MART YouTube dashboard."""

import csv
import io
from datetime import datetime, timedelta, timezone

from db.core import get_conn

_HEADERS = [
    "抓取时间",
    "视频名称",
    "视频URL",
    "视频发布时间",
    "视频浏览量",
    "视频点赞量",
    "视频评论数量",
]


def _int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def upsert_popmart_youtube_snapshot(snapshot_at, videos):
    """Store one fetch snapshot and update the cumulative current table."""
    with get_conn() as conn:
        conn.execute("UPDATE popmart_youtube_videos SET in_latest_100 = 0")
        for video in videos:
            video_id = video["video_id"]
            url = video.get("url") or f"https://www.youtube.com/watch?v={video_id}"
            title = video.get("title")
            published_at = video.get("published_at")
            view_count = _int_or_none(video.get("view_count"))
            like_count = _int_or_none(video.get("like_count"))
            comment_count = _int_or_none(video.get("comment_count"))
            error_message = video.get("error_message")
            success_at = snapshot_at if not error_message else None

            conn.execute(
                """INSERT INTO popmart_youtube_videos
                       (video_id, title, url, published_at, view_count, like_count, comment_count,
                        first_seen_at, last_seen_at, last_success_at, last_error, in_latest_100)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(video_id) DO UPDATE SET
                       title = COALESCE(excluded.title, popmart_youtube_videos.title),
                       url = excluded.url,
                       published_at = COALESCE(excluded.published_at, popmart_youtube_videos.published_at),
                       view_count = COALESCE(excluded.view_count, popmart_youtube_videos.view_count),
                       like_count = COALESCE(excluded.like_count, popmart_youtube_videos.like_count),
                       comment_count = COALESCE(excluded.comment_count, popmart_youtube_videos.comment_count),
                       last_seen_at = excluded.last_seen_at,
                       last_success_at = COALESCE(excluded.last_success_at, popmart_youtube_videos.last_success_at),
                       last_error = excluded.last_error,
                       in_latest_100 = 1""",
                (
                    video_id, title, url, published_at, view_count, like_count, comment_count,
                    snapshot_at, snapshot_at, success_at, error_message,
                ),
            )
            conn.execute(
                """INSERT OR REPLACE INTO popmart_youtube_snapshots
                       (snapshot_at, video_id, title, url, published_at,
                        view_count, like_count, comment_count, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_at, video_id, title, url, published_at,
                    view_count, like_count, comment_count, error_message,
                ),
            )


def get_latest_popmart_youtube_videos(limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM popmart_youtube_videos
               WHERE in_latest_100 = 1
               ORDER BY COALESCE(published_at, '') DESC, last_seen_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_popmart_youtube_videos():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM popmart_youtube_videos
               ORDER BY COALESCE(published_at, '') DESC, first_seen_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_popmart_youtube_snapshot_at():
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_at) AS snapshot_at FROM popmart_youtube_snapshots").fetchone()
    return row["snapshot_at"] if row and row["snapshot_at"] else None


def _previous_snapshot_at(before_at):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT MAX(snapshot_at) AS snapshot_at
               FROM popmart_youtube_snapshots
               WHERE snapshot_at < ?""",
            (before_at,),
        ).fetchone()
    return row["snapshot_at"] if row and row["snapshot_at"] else None

def get_popmart_youtube_weekly_summary(current_snapshot_at=None):
    current_snapshot_at = current_snapshot_at or get_latest_popmart_youtube_snapshot_at()
    if not current_snapshot_at:
        return {
            "snapshot_at": None,
            "previous_snapshot_at": None,
            "weekly_view_delta": 0,
            "weekly_new_videos": 0,
            "latest_count": 0,
            "failed_count": 0,
        }

    sgt = timezone(timedelta(hours=8))
    now = datetime.now(sgt)
    week_start_dt = datetime.combine(
        (now - timedelta(days=now.weekday())).date(),
        datetime.min.time(),
        tzinfo=sgt,
    )
    week_start_utc = week_start_dt.astimezone(timezone.utc).isoformat()
    previous_at = _previous_snapshot_at(week_start_utc)
    with get_conn() as conn:
        current_rows = conn.execute(
            "SELECT * FROM popmart_youtube_snapshots WHERE snapshot_at = ?",
            (current_snapshot_at,),
        ).fetchall()
        previous_rows = conn.execute(
            "SELECT video_id, view_count FROM popmart_youtube_snapshots WHERE snapshot_at = ?",
            (previous_at,),
        ).fetchall() if previous_at else []

    previous_views = {row["video_id"]: row["view_count"] or 0 for row in previous_rows}
    weekly_delta = 0
    failed_count = 0
    for row in current_rows:
        if row["error_message"]:
            failed_count += 1
        current_views = row["view_count"] or 0
        prior_views = previous_views.get(row["video_id"], 0)
        weekly_delta += max(current_views - prior_views, 0)

    week_start = week_start_dt.date().isoformat()
    week_end = (now + timedelta(days=1)).date().isoformat()
    weekly_new_videos = sum(
        1 for row in current_rows
        if row["published_at"] and week_start <= str(row["published_at"]) < week_end
    )

    return {
        "snapshot_at": current_snapshot_at,
        "previous_snapshot_at": previous_at,
        "weekly_view_delta": weekly_delta,
        "weekly_new_videos": weekly_new_videos,
        "latest_count": len(current_rows),
        "failed_count": failed_count,
    }


def get_popmart_youtube_snapshot_trend():
    """Return per-snapshot trend rows, skipping the first crawl baseline."""
    with get_conn() as conn:
        snapshot_rows = conn.execute(
            "SELECT DISTINCT snapshot_at FROM popmart_youtube_snapshots ORDER BY snapshot_at"
        ).fetchall()
        snapshots = [row["snapshot_at"] for row in snapshot_rows]
        rows_by_snapshot = {}
        for snapshot_at in snapshots:
            rows_by_snapshot[snapshot_at] = conn.execute(
                """SELECT video_id, published_at, view_count
                   FROM popmart_youtube_snapshots
                   WHERE snapshot_at = ?""",
                (snapshot_at,),
            ).fetchall()

    trend = []
    previous_views = None
    sgt = timezone(timedelta(hours=8))
    for snapshot_at in snapshots:
        rows = rows_by_snapshot[snapshot_at]
        current_views = {row["video_id"]: row["view_count"] or 0 for row in rows}
        if previous_views is None:
            previous_views = current_views
            continue

        view_delta = 0
        for video_id, view_count in current_views.items():
            view_delta += max(view_count - previous_views.get(video_id, 0), 0)

        dt = datetime.fromisoformat(snapshot_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_sgt = dt.astimezone(sgt)
        week_start = (dt_sgt - timedelta(days=dt_sgt.weekday())).date().isoformat()
        week_end = (dt_sgt + timedelta(days=1)).date().isoformat()
        weekly_new_videos = sum(
            1 for row in rows
            if row["published_at"] and week_start <= str(row["published_at"]) < week_end
        )

        trend.append({
            "snapshot_at": snapshot_at,
            "date": dt_sgt.date().isoformat(),
            "view_delta": view_delta,
            "weekly_new_videos": weekly_new_videos,
        })
        previous_views = current_views
    return trend

def build_popmart_youtube_csv(rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(_HEADERS)
    for row in rows:
        writer.writerow([
            row.get("last_seen_at") or row.get("snapshot_at") or "",
            row.get("title") or "",
            row.get("url") or "",
            row.get("published_at") or "",
            row.get("view_count") if row.get("view_count") is not None else "",
            row.get("like_count") if row.get("like_count") is not None else "",
            row.get("comment_count") if row.get("comment_count") is not None else "",
        ])
    return out.getvalue().encode("utf-8-sig")
