"""Fetch POP MART's latest YouTube video metrics for the dashboard."""

import json
import logging
import random
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import db
from config import YOUTUBE_COOKIES_FILE, SOCKS_PROXY

log = logging.getLogger(__name__)

SCRIPT_NAME = "popmart_youtube"
CHANNEL_VIDEOS_URL = "https://www.youtube.com/@POPMARTOFFICIAL/videos"
LATEST_LIMIT = 100
EXPECTED_INTERVAL_HOURS = 168
DETAIL_RETRIES = 3
DETAIL_DELAY_SECONDS = (2.0, 5.0)


def _video_id_from_url(url):
    parsed = urlparse(url or "")
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/") or None
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [None])[0]
    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/", 2)[2] or None
    return None


def _yt_opts(**extra):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if YOUTUBE_COOKIES_FILE:
        opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        opts["proxy"] = SOCKS_PROXY
    opts.update(extra)
    return opts


def _parse_upload_date(info):
    raw = info.get("upload_date") or info.get("release_date")
    if raw:
        raw = str(raw)
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        except Exception:
            return None
    return None


def fetch_latest_video_refs(limit=LATEST_LIMIT):
    try:
        import yt_dlp
    except Exception as exc:
        raise RuntimeError(f"yt-dlp unavailable: {exc}") from exc

    opts = _yt_opts(extract_flat=True, playlistend=limit, ignoreerrors=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL_VIDEOS_URL, download=False)

    entries = (info or {}).get("entries") or []
    refs = []
    seen = set()
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id") or _video_id_from_url(entry.get("url") or entry.get("webpage_url"))
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        refs.append({
            "video_id": video_id,
            "title": entry.get("title"),
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
        if len(refs) >= limit:
            break
    return refs


def fetch_video_detail(ref):
    try:
        import yt_dlp
    except Exception as exc:
        raise RuntimeError(f"yt-dlp unavailable: {exc}") from exc

    url = ref.get("url") or f"https://www.youtube.com/watch?v={ref['video_id']}"
    last_error = None
    for attempt in range(1, DETAIL_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(_yt_opts(noplaylist=True)) as ydl:
                info = ydl.extract_info(url, download=False)
            return {
                "video_id": ref["video_id"],
                "title": info.get("title") or ref.get("title"),
                "url": info.get("webpage_url") or url,
                "published_at": _parse_upload_date(info),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "comment_count": info.get("comment_count"),
                "error_message": None,
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < DETAIL_RETRIES:
                sleep_for = 10 * (3 ** (attempt - 1)) + random.uniform(0, 3)
                log.warning(
                    "POP MART YouTube detail failed for %s attempt %d/%d: %s; retrying in %.1fs",
                    url, attempt, DETAIL_RETRIES, last_error, sleep_for,
                )
                time.sleep(sleep_for)

    return {
        "video_id": ref["video_id"],
        "title": ref.get("title"),
        "url": url,
        "published_at": None,
        "view_count": None,
        "like_count": None,
        "comment_count": None,
        "error_message": last_error,
    }


def _format_int(value):
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _build_panels(summary, latest_rows):
    table_rows = []
    for row in latest_rows[:100]:
        table_rows.append([
            row.get("last_seen_at") or "",
            row.get("title") or "",
            row.get("url") or "",
            row.get("published_at") or "",
            _format_int(row.get("view_count")),
            _format_int(row.get("like_count")),
            _format_int(row.get("comment_count")),
        ])

    return [
        {
            "type": "metrics",
            "title": "POP MART YouTube weekly summary",
            "metrics": [
                {"label": "本周视频播放量", "value": _format_int(summary["weekly_view_delta"])},
                {"label": "本周视频发布数量", "value": _format_int(summary["weekly_new_videos"])},
                {"label": "最新视频数量", "value": _format_int(summary["latest_count"])},
                {"label": "抓取失败数量", "value": _format_int(summary["failed_count"])},
            ],
            "note": f"current={summary.get('snapshot_at') or '-'}; previous={summary.get('previous_snapshot_at') or '-'}",
        },
        {
            "type": "table",
            "title": "POP MART latest 100 videos",
            "headers": ["抓取时间", "视频名称", "视频URL", "视频发布时间", "视频浏览量", "视频点赞量", "视频评论数量"],
            "rows": table_rows,
        },
    ]


def run_popmart_youtube_fetch():
    snapshot_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    refs = fetch_latest_video_refs(LATEST_LIMIT)
    videos = []
    for idx, ref in enumerate(refs, start=1):
        if idx > 1:
            time.sleep(random.uniform(*DETAIL_DELAY_SECONDS))
        log.info("POP MART YouTube fetching detail %d/%d: %s", idx, len(refs), ref["url"])
        videos.append(fetch_video_detail(ref))

    db.upsert_popmart_youtube_snapshot(snapshot_at, videos)
    latest_rows = db.get_latest_popmart_youtube_videos(LATEST_LIMIT)
    all_rows = db.get_all_popmart_youtube_videos()
    summary = db.get_popmart_youtube_weekly_summary(snapshot_at)
    panels = _build_panels(summary, latest_rows)

    db.upsert_script_file(
        SCRIPT_NAME,
        "popmart_youtube_latest_100.csv",
        db.build_popmart_youtube_csv(latest_rows),
        file_key="latest_100",
    )
    db.upsert_script_file(
        SCRIPT_NAME,
        "popmart_youtube_all_history.csv",
        db.build_popmart_youtube_csv(all_rows),
        file_key="history_all",
    )
    db.upsert_script_report(
        SCRIPT_NAME,
        "ok",
        None,
        json.dumps(panels, ensure_ascii=False),
        EXPECTED_INTERVAL_HOURS,
    )
    return {"ok": True, "videos": len(videos), "failed": summary["failed_count"]}
