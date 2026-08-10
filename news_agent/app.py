"""Flask web UI for the news feed app."""

import base64
import io
import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import quote as _url_quote
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from logging.handlers import TimedRotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

import conference_db
import db
from config import ADMIN_EMAIL, EMAIL_WHITELIST, REPORT_API_KEY, SECRET_KEY, TOPIC_FETCH_HOUR_SGT, TRANSCRIPT_UPLOAD_MAX_MB
from email_digest import build_email_digest
from pipeline import run_fetch_and_summarize
from ai_digest import generate_batch_digest
from article_summarizer import summarize_single_article
from topic_workflow import run_topic_fetch
from conference_workflow import match_conferences_for_user, refresh_for_user

# ---------------------------------------------------------------------------
# Logging — console + rotating file
# ---------------------------------------------------------------------------

os.makedirs("logs", exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = TimedRotatingFileHandler(
    "logs/app.log",
    when="D",
    interval=3,       # new file every 3 days
    backupCount=7,    # keep 7 rotated files = 3 weeks
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
_file_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(_file_handler)

_request_logger = logging.getLogger("request")
_paste_logger = logging.getLogger("transcript_paste")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = TRANSCRIPT_UPLOAD_MAX_MB * 1024 * 1024

_TRANSCRIPT_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "uploads",
    "transcripts",
)
_TRANSCRIPT_PASTE_CHUNK_DIR = os.path.join(_TRANSCRIPT_UPLOAD_DIR, "paste_chunks")
_TRANSCRIPT_PASTE_CHUNK_MAX_CHARS = 150_000
_TRANSCRIPT_PASTE_CHUNK_MAX_PARTS = 10000
_ALLOWED_TRANSCRIPT_UPLOAD_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".mkv", ".webm", ".avi"
}

_SGT = timezone(timedelta(hours=8))


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(exc):
    return jsonify({
        "error": f"Upload is too large. Please use a file up to {TRANSCRIPT_UPLOAD_MAX_MB} MB."
    }), 413


@app.template_filter("to_sgt")
def to_sgt_filter(ts_str: str) -> str:
    """Convert a UTC ISO timestamp string to Singapore time (UTC+8) for display."""
    if not ts_str:
        return ""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_SGT).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_str[:19].replace("T", " ")
db.init_db()
conference_db.init_db()
db.close_open_fetch_logs()
db.seed_default_sources()

# Any jobs left in a transient state from before the last restart can never
# complete — the worker threads are gone. Reset them so users can retry.
with db.core.get_conn() as _conn:
    _conn.execute(
        "UPDATE transcript_jobs SET status='done' WHERE status IN ('summarizing', 'translating')"
    )
    _conn.execute(
        "UPDATE transcript_jobs SET status='error', error_message='Job interrupted by app restart'"
        " WHERE status IN ('processing', 'pending')"
    )


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@app.before_request
def start_request_timer():
    g.request_started_at = time.perf_counter()


@app.before_request
def load_current_user():
    uid = session.get("user_id")
    g.current_user = db.get_user_by_id(uid) if uid else None


@app.after_request
def log_app_request(response):
    if request.endpoint != "static":
        started = getattr(g, "request_started_at", None)
        duration_ms = int((time.perf_counter() - started) * 1000) if started else -1
        user = g.current_user["email"] if getattr(g, "current_user", None) else "anonymous"
        _request_logger.info(
            "method=%s path=%s status=%s duration_ms=%s remote=%s user=%s bytes=%s",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            request.headers.get("X-Forwarded-For") or request.remote_addr or "-",
            user,
            response.calculate_content_length() or 0,
        )
    return response


@app.context_processor
def inject_template_globals():
    return {
        "is_admin": bool(g.current_user and g.current_user["email"] == ADMIN_EMAIL)
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            wants_json = request.is_json or "application/json" in (request.headers.get("Accept") or "")
            if wants_json:
                return jsonify({"error": "Please sign in again before submitting this request."}), 401
            return redirect(url_for("identify", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _api_key_required():
    """Return a 403 response if the X-API-Key header doesn't match REPORT_API_KEY.
    Returns None if auth passes."""
    if not REPORT_API_KEY or request.headers.get("X-API-Key") != REPORT_API_KEY:
        return jsonify({"error": "forbidden"}), 403
    return None

# Background fetch lock (prevent concurrent fetches from overlapping)
_fetch_lock = threading.Lock()
_fetch_status: dict = {"running": False, "last_result": None}
_topic_fetch_status: dict = {"running": False, "last_result": None}
_conference_fetch_status: dict = {"running": False, "last_result": None}

# Digest jobs — keyed by UUID, each: {"status": "running"|"done"|"error", "result": str}
_digest_jobs: dict[str, dict] = {}

# Article translation jobs — keyed by UUID
_article_translation_jobs: dict[str, dict] = {}

_pdf_tool_jobs: dict[str, dict] = {}
_PDF_TOOL_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "uploads",
    "pdf_tools",
)
_PDF_TOOL_META_FILENAME = "meta.json"
_PDF_TOOL_OUTPUT_FILENAME = "bilingual.pdf"
_PDF_TOOL_TRANSLATED_FILENAME = "translated.md"

# ---------------------------------------------------------------------------
# Periodic background scheduler
# ---------------------------------------------------------------------------

def _scheduled_daily_fetch():
    """Full fetch (all sources). Runs at 05:00 SGT (21:00 UTC, server is UTC+8)."""
    if _fetch_status["running"]:
        logger_sched.info("Skipping scheduled daily fetch — manual fetch in progress")
        return
    _fetch_status["running"] = True
    logger_sched.info("Scheduled daily fetch starting…")
    log_id = db.log_fetch_start(trigger="scheduled")
    try:
        result = run_fetch_and_summarize()
        db.log_fetch_finish(log_id, result)
        logger_sched.info("Scheduled daily fetch done: %d new article(s)", result["total_new"])
    except Exception as exc:
        db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
        logger_sched.error("Scheduled daily fetch error: %s", exc)
    finally:
        _fetch_status["running"] = False


def _scheduled_topic_fetch():
    """Topic fetch runs on its own schedule and does not reuse the source scheduler state."""
    if _topic_fetch_status["running"]:
        logger_sched.info("Skipping scheduled topic fetch - topic fetch already in progress")
        return
    _topic_fetch_status["running"] = True
    logger_sched.info("Scheduled topic fetch starting")
    log_id = db.log_fetch_start(trigger="scheduled-topics")
    try:
        result = run_topic_fetch()
        db.log_fetch_finish(log_id, result)
        _topic_fetch_status["last_result"] = {
            "status": "ok",
            "new_articles": result["total_new"],
            "sources": result["sources"],
        }
        logger_sched.info("Scheduled topic fetch done: %d new item(s)", result["total_new"])
    except Exception as exc:
        db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
        _topic_fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        logger_sched.error("Scheduled topic fetch error: %s", exc)
    finally:
        _topic_fetch_status["running"] = False


def _scheduled_digest_send():
    """Send due digest emails. Runs at 09:00 SGT (01:00 UTC). No pre-fetch — relies on the 5am fetch."""
    from email_sender import send_digest as _send_email
    from ai_digest import generate_batch_digest

    # Migrate any users who have legacy digest_enabled but no presets yet.
    for user in [dict(u) for u in db.get_users_due_for_digest()]:
        if not db.get_digest_presets(user["id"]):
            followed = db.get_followed_source_ids(user["id"])
            db.create_digest_preset(user["id"], "简报 1", followed)
            db.update_digest_preset(
                db.get_digest_presets(user["id"])[0]["id"],
                user["id"], "简报 1", followed,
                digest_enabled=1,
                digest_frequency_days=user["digest_frequency_days"] or 7,
            )
            logger_sched.info("Auto-created preset for legacy user %s", user["email"])

    presets_due = db.get_presets_due_for_email()
    if not presets_due:
        return
    date_to = date.today().isoformat()
    for preset in presets_due:
        preset_date_from = (date.today() - timedelta(days=preset["digest_frequency_days"])).isoformat()
        source_ids = preset["source_ids"] or None
        try:
            articles = db.get_articles(date_from=preset_date_from, date_to=date_to, source_ids=source_ids)
            article_ids = [a["id"] for a in articles]
            if not article_ids:
                logger_sched.info("No articles for preset %d (%s), skipping", preset["id"], preset["user_email"])
                db.update_preset_last_sent(preset["id"])
                continue
            logger_sched.info(
                "Generating preset digest '%s' for %s (%d articles)",
                preset["name"], preset["user_email"], len(article_ids),
            )
            md = generate_batch_digest(article_ids, user_id=preset["user_id"])
            ok = _send_email(md, to_email=preset["user_email"], date_label=f"{preset_date_from} to {date_to}")
            if ok:
                db.update_preset_last_sent(preset["id"])
                logger_sched.info("Preset digest '%s' sent to %s", preset["name"], preset["user_email"])
        except Exception as exc:
            logger_sched.error("Preset digest %d failed for %s: %s", preset["id"], preset["user_email"], exc)


def _scheduled_raw_feed_send():
    """Send due raw-feed digest emails independently from the AI digest presets."""
    from email_sender import send_digest as _send_email
    from raw_feed_digest import build_raw_feed_digest, date_range_for_frequency

    subscriptions_due = db.get_raw_feed_subscriptions_due()
    if not subscriptions_due:
        return

    for sub in subscriptions_due:
        date_from, date_to = date_range_for_frequency(sub["frequency_days"])
        try:
            markdown_body = build_raw_feed_digest(
                sub["topic_ids"],
                date_from=date_from,
                date_to=date_to,
                user_id=sub["user_id"],
                source_ids=sub.get("source_ids") or [],
            )
            if not markdown_body.strip():
                logger_sched.info(
                    "No raw-feed videos for subscription %d (%s), skipping",
                    sub["id"], sub["user_email"],
                )
                db.update_raw_feed_subscription_last_sent(sub["id"])
                continue

            subject = f"新增信息流日报 — {date_from} to {date_to}"
            ok = _send_email(
                markdown_body,
                to_email=sub["user_email"],
                date_label=f"{date_from} to {date_to}",
                subject=subject,
            )
            if ok:
                db.update_raw_feed_subscription_last_sent(sub["id"])
                logger_sched.info("Raw-feed digest sent to %s", sub["user_email"])
        except Exception as exc:
            logger_sched.error("Raw-feed subscription %d failed for %s: %s", sub["id"], sub["user_email"], exc)

def _send_preset_digest_now(preset_id: int) -> None:
    """Admin-triggered full send for one AI digest preset."""
    from email_sender import send_digest as _send_email
    from ai_digest import generate_batch_digest

    preset = db.get_digest_preset_for_admin(preset_id)
    if not preset:
        logger_sched.warning("Admin send skipped: preset %d not found", preset_id)
        return

    try:
        logger_sched.info("Admin-triggered AI digest fetch starting for preset %d", preset_id)
        run_fetch_and_summarize()
        date_to = date.today().isoformat()
        date_from = (date.today() - timedelta(days=preset["digest_frequency_days"])).isoformat()
        source_ids = preset["source_ids"] or None
        articles = db.get_articles(date_from=date_from, date_to=date_to, source_ids=source_ids)
        article_ids = [a["id"] for a in articles]
        if not article_ids:
            logger_sched.info("Admin-triggered AI digest found no articles for preset %d", preset_id)
            return
        markdown_body = generate_batch_digest(article_ids, user_id=preset["user_id"])
        ok = _send_email(
            markdown_body,
            to_email=preset["user_email"],
            date_label=f"{date_from} to {date_to}",
            subject=f"AI 简报 — {preset['name']} — {date_from} to {date_to}",
        )
        if ok:
            db.update_preset_last_sent(preset_id)
            logger_sched.info("Admin-triggered AI digest sent: preset %d to %s", preset_id, preset["user_email"])
    except Exception as exc:
        logger_sched.error("Admin-triggered AI digest failed for preset %d: %s", preset_id, exc)


def _send_raw_feed_now(user_id: int) -> None:
    """Admin-triggered send for one user's raw-feed digest using existing items."""
    from email_sender import send_digest as _send_email
    from raw_feed_digest import build_raw_feed_digest, date_range_for_frequency

    user = db.get_user_by_id(user_id)
    if not user:
        logger_sched.warning("Admin raw-feed send skipped: user %d not found", user_id)
        return

    try:
        sub = db.get_raw_feed_subscription(user_id)
        date_from, date_to = date_range_for_frequency(sub["frequency_days"])
        markdown_body = build_raw_feed_digest(
            sub["topic_ids"],
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
            source_ids=sub.get("source_ids") or [],
        )
        if not markdown_body.strip():
            logger_sched.info("Admin-triggered raw feed found no videos for user %d", user_id)
            return
        ok = _send_email(
            markdown_body,
            to_email=user["email"],
            date_label=f"{date_from} to {date_to}",
            subject=f"新增信息流日报 — {date_from} to {date_to}",
        )
        if ok:
            db.update_raw_feed_subscription_last_sent(sub["id"])
            logger_sched.info("Admin-triggered raw feed sent to %s", user["email"])
    except Exception as exc:
        logger_sched.error("Admin-triggered raw feed failed for user %d: %s", user_id, exc)
logger_sched = logging.getLogger("scheduler")
logger_dashboard = logging.getLogger("dashboard")

_gpu_fetch_lock = threading.Lock()
_gpu_fetch_running = False
_GPU_STATUS_SCRIPT_NAME = "GPU算力价格指数"
_GPU_STATUS_PANEL_ID = "panel-gpu-prices"
_GPU_STATUS_INTERVAL_HOURS = 24
_llm_token_index_fetch_lock = threading.Lock()
_llm_token_index_fetch_running = False
_LLM_TOKEN_INDEX_SCRIPT_NAME = "LLM Token Expenditure Index"
_LLM_TOKEN_INDEX_INTERVAL_HOURS = 24
_popmart_youtube_fetch_lock = threading.Lock()
_popmart_youtube_fetch_running = False
_POPMART_YOUTUBE_SCRIPT_NAME = "popmart_youtube"
_POPMART_YOUTUBE_INTERVAL_HOURS = 168


def _run_gpu_price_fetch():
    """Fetch GPU price index data from api.ornnai.com and cache in DB."""
    global _gpu_fetch_running
    if not _gpu_fetch_lock.acquire(blocking=False):
        logger_dashboard.info("GPU price fetch already running — skipped")
        return
    _gpu_fetch_running = True
    try:
        from fetchers.gpu_prices import fetch_all_gpu_prices
        logger_dashboard.info("GPU price fetch starting…")
        results = fetch_all_gpu_prices()
        for gpu_type, data in results.items():
            db.upsert_gpu_price_data(gpu_type, data)
        db.upsert_script_report(
            _GPU_STATUS_SCRIPT_NAME,
            "ok",
            None,
            None,
            _GPU_STATUS_INTERVAL_HOURS,
        )
        logger_dashboard.info("GPU price fetch done: %d GPU type(s) updated", len(results))
    except Exception as exc:
        logger_dashboard.error("GPU price fetch error: %s", exc)
        db.upsert_script_report(
            _GPU_STATUS_SCRIPT_NAME,
            "error",
            str(exc),
            None,
            _GPU_STATUS_INTERVAL_HOURS,
        )
    finally:
        _gpu_fetch_running = False
        _gpu_fetch_lock.release()


def _with_dashboard_anchor(panel: dict) -> dict:
    panel = dict(panel)
    if panel["script_name"] == _GPU_STATUS_SCRIPT_NAME:
        panel["anchor_id"] = _GPU_STATUS_PANEL_ID
    else:
        panel["anchor_id"] = f"panel-{panel['script_name'].replace(' ', '-')}"
    return panel


def _clean_dashboard_report_panels(panel: dict) -> dict:
    panel = dict(panel)
    if panel["script_name"] == _LLM_TOKEN_INDEX_SCRIPT_NAME:
        panel["panels"] = [
            script_panel
            for script_panel in (panel.get("panels") or [])
            if script_panel.get("type") != "table"
        ]
    return panel


def _build_gpu_status_panel_fallback() -> dict | None:
    last_updated = db.get_gpu_price_last_updated()
    if not last_updated:
        return None
    pushed_at = datetime.fromisoformat(last_updated)
    if pushed_at.tzinfo is None:
        pushed_at = pushed_at.replace(tzinfo=timezone.utc)
    hours_since = (datetime.now(timezone.utc) - pushed_at).total_seconds() / 3600
    return {
        "script_name": _GPU_STATUS_SCRIPT_NAME,
        "status": "ok",
        "error_message": None,
        "panels": [],
        "pushed_at": last_updated,
        "expected_interval_hours": _GPU_STATUS_INTERVAL_HOURS,
        "is_overdue": hours_since > _GPU_STATUS_INTERVAL_HOURS,
    }


def _run_openrouter_usage_fetch() -> dict:
    """Fetch weekly OpenRouter token-usage data and push panels + Excel to the dashboard.

    Runs in-process (no HTTP round-trip to our own /api/report — this server
    IS the dashboard), writing directly to the same script_reports/script_files
    tables that the external-script API route writes to.

    Returns {"ok": True, "panels": N} or {"ok": False, "error": "..."} so
    callers (the manual-refresh API route) can report the actual result
    instead of always claiming success.
    """
    import json as _json
    import traceback as _traceback
    try:
        from fetchers.openrouter_usage import run_openrouter_usage_fetch
        logger_dashboard.info("OpenRouter usage fetch starting…")
        panels, excel_bytes = run_openrouter_usage_fetch()
        db.upsert_script_report(
            "openrouter_usage", "ok", None, _json.dumps(panels), 168,  # 168h = 7 days
        )
        db.upsert_script_file("openrouter_usage", "openrouter_usage.xlsx", excel_bytes)
        logger_dashboard.info("OpenRouter usage fetch done: %d panels", len(panels))
        return {"ok": True, "panels": len(panels)}
    except Exception as exc:
        logger_dashboard.error("OpenRouter usage fetch error: %s", _traceback.format_exc())
        db.upsert_script_report("openrouter_usage", "error", str(exc), None, 168)
        return {"ok": False, "error": str(exc)}


def _run_vercel_labs_fetch() -> dict:
    """Fetch daily Vercel AI Gateway Labs data and push panels + Excel."""
    import json as _json
    import traceback as _traceback
    try:
        from fetchers.vercel_labs import run_vercel_labs_fetch
        logger_dashboard.info("Vercel labs fetch starting...")
        panels, excel_bytes = run_vercel_labs_fetch()
        db.upsert_script_report(
            "vercel_labs", "ok", None, _json.dumps(panels), 24,
        )
        db.upsert_script_file("vercel_labs", "vercel_labs.xlsx", excel_bytes)
        logger_dashboard.info("Vercel labs fetch done: %d panels", len(panels))
        return {"ok": True, "panels": len(panels)}
    except Exception as exc:
        logger_dashboard.error("Vercel labs fetch error: %s", _traceback.format_exc())
        db.upsert_script_report("vercel_labs", "error", str(exc), None, 24)
        return {"ok": False, "error": str(exc)}


def _run_popmart_youtube_fetch() -> dict:
    """Fetch POP MART YouTube metrics and publish dashboard files."""
    import traceback as _traceback
    global _popmart_youtube_fetch_running
    if not _popmart_youtube_fetch_lock.acquire(blocking=False):
        logger_dashboard.info("POP MART YouTube fetch already running - skipped")
        return {"ok": False, "error": "already_running"}

    _popmart_youtube_fetch_running = True
    try:
        from fetchers.popmart_youtube import run_popmart_youtube_fetch

        logger_dashboard.info("POP MART YouTube fetch starting...")
        result = run_popmart_youtube_fetch()
        logger_dashboard.info(
            "POP MART YouTube fetch done: %d videos, %d failed",
            result.get("videos", 0), result.get("failed", 0),
        )
        return result
    except Exception as exc:
        logger_dashboard.error("POP MART YouTube fetch error: %s", _traceback.format_exc())
        db.upsert_script_report(
            _POPMART_YOUTUBE_SCRIPT_NAME,
            "error",
            str(exc),
            None,
            _POPMART_YOUTUBE_INTERVAL_HOURS,
        )
        return {"ok": False, "error": str(exc)}
    finally:
        _popmart_youtube_fetch_running = False
        _popmart_youtube_fetch_lock.release()

def _run_llm_token_expenditure_index_fetch() -> dict:
    """Fetch the latest Silicon Data token index snapshot and extend local history."""
    import json as _json
    import traceback as _traceback
    global _llm_token_index_fetch_running
    if not _llm_token_index_fetch_lock.acquire(blocking=False):
        logger_dashboard.info("LLM token expenditure index fetch already running - skipped")
        return {"ok": False, "error": "already_running"}

    _llm_token_index_fetch_running = True
    try:
        from fetchers.llm_token_expenditure_index import run_llm_token_expenditure_index_fetch

        logger_dashboard.info("LLM token expenditure index fetch starting...")
        panels, excel_bytes = run_llm_token_expenditure_index_fetch()
        db.upsert_script_report(
            _LLM_TOKEN_INDEX_SCRIPT_NAME,
            "ok",
            None,
            _json.dumps(panels),
            _LLM_TOKEN_INDEX_INTERVAL_HOURS,
        )
        db.upsert_script_file(
            _LLM_TOKEN_INDEX_SCRIPT_NAME,
            "llm_token_expenditure_index.xlsx",
            excel_bytes,
        )
        logger_dashboard.info("LLM token expenditure index fetch done: %d panels", len(panels))
        return {"ok": True, "panels": len(panels)}
    except Exception as exc:
        logger_dashboard.error("LLM token expenditure index fetch error: %s", _traceback.format_exc())
        db.upsert_script_report(
            _LLM_TOKEN_INDEX_SCRIPT_NAME,
            "error",
            str(exc),
            None,
            _LLM_TOKEN_INDEX_INTERVAL_HOURS,
        )
        return {"ok": False, "error": str(exc)}
    finally:
        _llm_token_index_fetch_running = False
        _llm_token_index_fetch_lock.release()


def _bootstrap_dashboard_datasets() -> None:
    """Seed dashboard datasets that should exist even on a fresh deployment.

    Runs in a background thread so startup stays responsive. Each dataset is
    fetched only when it has no stored report yet, preserving the normal
    cumulative history/update behavior for subsequent runs.
    """
    try:
        existing = {row["script_name"] for row in db.get_all_script_reports()}
        if _LLM_TOKEN_INDEX_SCRIPT_NAME not in existing:
            logger_dashboard.info("Bootstrapping missing dashboard dataset: %s", _LLM_TOKEN_INDEX_SCRIPT_NAME)
            _run_llm_token_expenditure_index_fetch()
        if _POPMART_YOUTUBE_SCRIPT_NAME not in existing:
            import json as _json
            db.upsert_script_report(
                _POPMART_YOUTUBE_SCRIPT_NAME,
                "ok",
                None,
                _json.dumps([
                    {
                        "type": "metrics",
                        "title": "POP MART YouTube weekly summary",
                        "metrics": [
                            {"label": "本周视频播放量", "value": "-"},
                            {"label": "本周视频发布数量", "value": "-"},
                            {"label": "最新视频数量", "value": "0"},
                            {"label": "抓取失败数量", "value": "0"},
                        ],
                        "note": "等待首次抓取",
                    },
                    {
                        "type": "table",
                        "title": "POP MART latest 100 videos",
                        "headers": ["抓取时间", "视频名称", "视频URL", "视频发布时间", "视频浏览量", "视频点赞量", "视频评论数量"],
                        "rows": [],
                    },
                ], ensure_ascii=False),
                _POPMART_YOUTUBE_INTERVAL_HOURS,
            )
    except Exception:
        logger_dashboard.exception("Dashboard bootstrap failed")


def _llm_token_index_report_needs_refresh(report: dict | None) -> bool:
    if not report:
        return True
    for panel in report.get("panels") or []:
        labels = {dataset.get("label") for dataset in (panel.get("datasets") or [])}
        if any(label and label.startswith("TrakToken") for label in labels):
            return False
    return True


def _ensure_llm_token_index_report() -> None:
    """Guarantee the LLM token index dashboard includes the latest source mix.

    This is intentionally request-safe and DB-backed: if the report is missing,
    or if it predates the TrakToken dataset addition, we refresh it immediately
    so the dashboard can render the combined series without waiting for the next
    scheduled run.
    """
    try:
        report = next(
            (row for row in db.get_all_script_reports() if row["script_name"] == _LLM_TOKEN_INDEX_SCRIPT_NAME),
            None,
        )
        if _llm_token_index_report_needs_refresh(report):
            logger_dashboard.info("Refreshing dashboard dataset for %s", _LLM_TOKEN_INDEX_SCRIPT_NAME)
            _run_llm_token_expenditure_index_fetch()
    except Exception:
        logger_dashboard.exception("Failed to ensure LLM token expenditure index report")


# Explicit SGT timezone so all cron hours are unambiguous regardless of server clock.
_scheduler = BackgroundScheduler(daemon=True, timezone="Asia/Singapore")

_scheduler.add_job(_scheduled_daily_fetch, "cron", hour=5,  minute=0, id="daily_fetch")   # 05:00 SGT
_scheduler.add_job(_scheduled_topic_fetch, "cron", hour=TOPIC_FETCH_HOUR_SGT, minute=0, id="topic_fetch")
_scheduler.add_job(_scheduled_digest_send, "cron", hour=9,  minute=0, id="digest_send")   # 09:00 SGT
_scheduler.add_job(_scheduled_raw_feed_send, "cron", hour=9, minute=5, id="raw_feed_send") # 09:05 SGT
_scheduler.add_job(                                                                         # 09:00 SGT
    lambda: _run_gpu_price_fetch(),
    "cron", hour=9, minute=0, id="gpu_price_daily",
)
_scheduler.add_job(                                                                         # every 168h from a fixed anchor
    lambda: _run_openrouter_usage_fetch(),
    "interval", hours=168, id="openrouter_usage_weekly",
    # Fixed anchor (not "now") so the schedule is stable across server restarts/deploys —
    # APScheduler computes next-run as anchor + N*168h for whatever N is next in the future,
    # rather than resetting the countdown to "restart time + 168h" every time this job is
    # re-registered at process startup.
    start_date="2026-06-29 09:30:00",
)
_scheduler.add_job(
    lambda: _run_vercel_labs_fetch(),
    "cron", hour=9, minute=35, id="vercel_labs_daily",
)
_scheduler.add_job(
    lambda: _run_llm_token_expenditure_index_fetch(),
    "cron", hour=9, minute=40, id="llm_token_expenditure_index_daily",
)
_scheduler.add_job(
    lambda: _run_popmart_youtube_fetch(),
    "cron", day_of_week="mon", hour=10, minute=10, id="popmart_youtube_weekly",
)

_scheduler.start()
threading.Thread(target=_bootstrap_dashboard_datasets, daemon=True).start()


# ---------------------------------------------------------------------------
# Identity (email-only, no password)
# ---------------------------------------------------------------------------

@app.route("/identify", methods=["GET", "POST"])
def identify():
    if g.current_user:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email or "@" not in email:
            error = "Please enter a valid email address."
        elif EMAIL_WHITELIST and email.lower() not in EMAIL_WHITELIST:
            error = "This email is not authorised to access this app."
        else:
            user = db.get_or_create_user(email)
            # Auto-follow all sources for users who have no follows yet,
            # so the feed and sources page always reflect the same state.
            if not db.get_followed_source_ids(user["id"]):
                for s in db.get_all_sources():
                    db.follow_source(user["id"], s["id"])
            if not db.get_followed_topic_ids(user["id"]):
                for topic in db.get_all_topics(active_only=True):
                    db.follow_topic(user["id"], topic["id"])
            session["user_id"] = user["id"]
            next_url = request.args.get("next", "")
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
    return render_template("identify.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("identify"))


# ---------------------------------------------------------------------------
# News feed
# ---------------------------------------------------------------------------

def _decorate_topic_items(rows: list[dict]) -> list[dict]:
    source_map = db.get_topic_item_sources_bulk([row["id"] for row in rows])
    decorated: list[dict] = []
    for row in rows:
        item = dict(row)
        item["source_name"] = item.pop("topic_name")
        item["source_type"] = "topic"
        item["kind"] = "topic_item"
        item["provenance"] = source_map.get(item["id"], [])
        item["translated_content"] = None
        decorated.append(item)
    return decorated

@app.route("/")
@login_required
def index():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    date_from = request.args.get("date_from", week_ago)
    date_to = request.args.get("date_to", today)
    source_filter_submitted = request.args.get("source_filter") == "1"
    topic_filter_submitted = request.args.get("topic_filter") == "1"
    selected_source_ids = request.args.getlist("source_ids", type=int)
    selected_topic_ids = request.args.getlist("topic_ids", type=int)

    all_sources = db.get_all_sources()
    all_topics = db.get_all_topics(active_only=True)
    followed_ids = db.get_followed_source_ids(g.current_user["id"])
    followed_topic_ids = db.get_followed_topic_ids(g.current_user["id"])
    if all_topics and not followed_topic_ids:
        for topic in all_topics:
            db.follow_topic(g.current_user["id"], topic["id"])
        followed_topic_ids = db.get_followed_topic_ids(g.current_user["id"])

    source_ids = selected_source_ids if source_filter_submitted else (followed_ids if followed_ids else None)
    topic_ids = selected_topic_ids if topic_filter_submitted else (followed_topic_ids if followed_topic_ids else None)
    active_topic_names = set()
    if not (topic_filter_submitted and not selected_topic_ids):
        active_topic_id_set = set(topic_ids) if topic_ids else None
        active_topic_names = {
            topic["name"].strip().casefold()
            for topic in all_topics
            if topic.get("name") and (active_topic_id_set is None or topic["id"] in active_topic_id_set)
        }

    source_articles = [] if source_filter_submitted and not selected_source_ids else db.get_articles(
        date_from=date_from,
        date_to=date_to,
        source_ids=source_ids,
    )
    if active_topic_names:
        source_articles = [
            article for article in source_articles
            if not (
                article["source_type"] == "nitter"
                and (article["source_name"] or "").strip().casefold() in active_topic_names
            )
        ]
    topic_items = [] if topic_filter_submitted and not selected_topic_ids else db.get_topic_feed_items(
        date_from=date_from,
        date_to=date_to,
        topic_ids=topic_ids,
    )
    articles_list = sorted(
        [*[dict(a) for a in source_articles], *_decorate_topic_items([dict(a) for a in topic_items])],
        key=lambda item: item.get("published_at") or item.get("fetched_at") or "",
        reverse=True,
    )

    # Group articles by source, preserving first-appearance order
    source_order: dict[str, int] = {}
    grouped_articles: list[dict] = []
    for art in articles_list:
        sname = art["source_name"]
        if sname not in source_order:
            source_order[sname] = len(grouped_articles)
            grouped_articles.append({
                "name": sname,
                "type": art["source_type"],
                "articles": [],
            })
        grouped_articles[source_order[sname]]["articles"].append(art)

    non_nitter_groups = [g for g in grouped_articles if g["type"] != "nitter"]
    nitter_groups = [g for g in grouped_articles if g["type"] == "nitter"]

    # Auto-create 2 default presets for users who predate the preset system.
    # Preset 1 inherits the user's existing digest_enabled + frequency so their
    # old auto-email schedule carries over without any manual reconfiguration.
    uid = g.current_user["id"]
    digest_presets = db.get_digest_presets(uid)
    if not digest_presets:
        default_sources = list(followed_ids) if followed_ids else []
        p1 = db.create_digest_preset(uid, "简报 1", default_sources)
        if p1:
            db.update_digest_preset(
                p1["id"], uid, "简报 1", default_sources,
                digest_enabled=1 if g.current_user["digest_enabled"] else 0,
                digest_frequency_days=g.current_user["digest_frequency_days"] or 7,
            )
        db.create_digest_preset(uid, "简报 2", default_sources)
        digest_presets = db.get_digest_presets(uid)
    elif g.current_user["digest_enabled"] and not any(p["digest_enabled"] for p in digest_presets):
        # One-time migration: presets were created before the inherit-fix was deployed.
        # User has an active digest schedule at the user level but all presets are disabled.
        # Carry the user-level settings into Preset 1 so auto-email keeps working.
        p1 = digest_presets[0]
        db.update_digest_preset(
            p1["id"], uid, p1["name"], p1["source_ids"],
            digest_enabled=1,
            digest_frequency_days=g.current_user["digest_frequency_days"] or 7,
        )
        digest_presets = db.get_digest_presets(uid)

    return render_template(
        "index.html",
        articles=articles_list,
        non_nitter_groups=non_nitter_groups,
        nitter_groups=nitter_groups,
        all_sources=[dict(s) for s in all_sources],
        all_topics=all_topics,
        selected_source_ids=selected_source_ids,
        selected_topic_ids=selected_topic_ids,
        source_filter_submitted=source_filter_submitted,
        topic_filter_submitted=topic_filter_submitted,
        followed_source_ids=followed_ids,
        followed_topic_ids=followed_topic_ids,
        date_from=date_from,
        date_to=date_to,
        fetch_status=_fetch_status,
    )


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------

@app.route("/sources", methods=["GET", "POST"])
@login_required
def sources():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        type_ = request.form.get("type", "").strip()
        raw_url = request.form.get("url", "").strip()
        url_filter = request.form.get("url_filter", "").strip() or None

        if not name or not type_ or not raw_url:
            error = "All fields are required."
        elif type_ not in ("rss", "nitter", "web", "youtube"):
            error = "Type must be rss, nitter, web, or youtube."
        else:
            # For nitter, store only the handle (strip full x.com/twitter.com URLs)
            if type_ == "nitter":
                import re as _re
                handle = _re.sub(r"https?://(www\.)?(x|twitter)\.com/", "", raw_url).rstrip("/").lstrip("@").split("/")[0]
                url = f"nitter:{handle}"
            else:
                url = raw_url
            try:
                db.upsert_source(name=name, type_=type_, url=url, url_filter=url_filter)
                return redirect(url_for("sources"))
            except Exception as exc:
                error = f"Error saving source: {exc}"

    all_sources = db.get_all_sources()
    followed_ids = db.get_followed_source_ids(g.current_user["id"])
    return render_template("sources.html", sources=all_sources, error=error,
                           followed_source_ids=followed_ids)


def _sync_follow_change_to_presets(user_id: int, source_id: int, action: str):
    """Propagate a single follow/unfollow to all of the user's digest presets."""
    for p in db.get_digest_presets(user_id):
        ids = set(p["source_ids"])
        if action == "unfollow":
            ids.discard(source_id)
        else:
            ids.add(source_id)
        db.update_digest_preset(
            p["id"], user_id, p["name"], sorted(ids),
            digest_enabled=int(p["digest_enabled"]),
            digest_frequency_days=p["digest_frequency_days"],
        )


def _sync_follows_to_all_presets(user_id: int, source_ids: list):
    """Replace source_ids on all of the user's presets with the given list."""
    for p in db.get_digest_presets(user_id):
        db.update_preset_source_ids(p["id"], source_ids)


def _sync_presets_to_follows(user_id: int):
    """Set the user's follow list to the union of all their preset source_ids."""
    presets = db.get_digest_presets(user_id)
    union_ids = sorted({sid for p in presets for sid in p["source_ids"]})
    db.set_user_follows(user_id, union_ids)

def _sync_raw_feed_to_topic_follows(user_id: int, topic_ids: list[int]):
    """Keep the main topic follow list aligned with the raw-feed selection."""
    db.set_user_topic_follows(user_id, topic_ids)


def _sync_topic_follow_change_to_raw_feed(user_id: int, topic_id: int, action: str):
    """Propagate topic follow/unfollow changes into the user's raw-feed subscription."""
    sub = db.get_raw_feed_subscription(user_id)
    ids = set(sub["topic_ids"])
    if action == "unfollow":
        ids.discard(topic_id)
    else:
        ids.add(topic_id)
    db.update_raw_feed_subscription(
        user_id,
        sorted(ids),
        enabled=sub["enabled"],
        frequency_days=sub["frequency_days"],
        source_ids=sub.get("source_ids") or [],
    )


@app.route("/sources/<int:source_id>/follow", methods=["POST"])
@login_required
def toggle_follow(source_id: int):
    action = request.form.get("action", "follow")
    uid = g.current_user["id"]
    if action == "unfollow":
        db.unfollow_source(uid, source_id)
    else:
        db.follow_source(uid, source_id)
    _sync_follow_change_to_presets(uid, source_id, action)
    return redirect(url_for("sources"))


def _start_topic_fetch_job(
    topic_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    trigger: str = "manual-topic",
) -> bool:
    if _topic_fetch_status["running"]:
        return False

    def _run():
        _topic_fetch_status["running"] = True
        log_id = db.log_fetch_start(trigger=trigger)
        try:
            result = run_topic_fetch(topic_ids=[topic_id], date_from=date_from, date_to=date_to)
            db.log_fetch_finish(log_id, result)
            _topic_fetch_status["last_result"] = {
                "status": "ok",
                "new_articles": result["total_new"],
                "sources": result["sources"],
            }
        except Exception as exc:
            logging.exception("Topic fetch error")
            db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
            _topic_fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        finally:
            _topic_fetch_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return True

@app.route("/topics", methods=["GET", "POST"])
@login_required
def topics():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        aliases_raw = request.form.get("aliases", "").strip()
        aliases = [part.strip() for part in re.split(r"[\n,]+", aliases_raw) if part.strip()]
        channels = ["youtube"]
        backfill_date_from = request.form.get("backfill_date_from", "").strip() or None
        backfill_date_to = request.form.get("backfill_date_to", "").strip() or None
        if not name:
            error = "Topic name is required."
        else:
            try:
                topic = db.create_topic(
                    name=name,
                    aliases=aliases,
                    channels=channels,
                    backfill_date_from=backfill_date_from,
                    backfill_date_to=backfill_date_to,
                )
                db.follow_topic(g.current_user["id"], topic["id"])
                has_backfill = bool(backfill_date_from or backfill_date_to)
                if has_backfill:
                    _start_topic_fetch_job(
                        topic["id"],
                        date_from=backfill_date_from,
                        date_to=backfill_date_to,
                        trigger="create-topic-backfill",
                    )
                return redirect(url_for("topics"))
            except Exception as exc:
                error = f"Error saving topic: {exc}"

    all_topics = db.get_all_topics()
    followed_topic_ids = db.get_followed_topic_ids(g.current_user["id"])
    return render_template(
        "topics.html",
        topics=all_topics,
        error=error,
        followed_topic_ids=followed_topic_ids,
        topic_fetch_status=_topic_fetch_status,
    )


@app.route("/topics/<int:topic_id>/queries", methods=["POST"])
@login_required
def update_topic_queries(topic_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return redirect(url_for("topics"))
    topic = db.get_topic_by_id(topic_id)
    if not topic:
        return redirect(url_for("topics"))
    aliases_raw = request.form.get("aliases", "").strip()
    aliases = [part.strip() for part in re.split(r"[\n,]+", aliases_raw) if part.strip()]
    db.update_topic(
        topic_id=topic_id,
        name=topic["name"],
        aliases=aliases,
        channels=["youtube"],
        active=topic["active"],
        backfill_date_from=topic.get("backfill_date_from"),
        backfill_date_to=topic.get("backfill_date_to"),
    )
    return redirect(url_for("topics"))

@app.route("/topics/<int:topic_id>/follow", methods=["POST"])
@login_required
def toggle_topic_follow(topic_id: int):
    action = request.form.get("action", "follow")
    uid = g.current_user["id"]
    if action == "unfollow":
        db.unfollow_topic(uid, topic_id)
    else:
        db.follow_topic(uid, topic_id)
    _sync_topic_follow_change_to_raw_feed(uid, topic_id, action)
    return redirect(url_for("topics"))


@app.route("/topics/<int:topic_id>/delete", methods=["POST"])
@login_required
def delete_topic(topic_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return redirect(url_for("topics"))
    db.delete_topic(topic_id)
    return redirect(url_for("topics"))


@app.route("/conferences")
@login_required
def conferences():
    uid = g.current_user["id"]
    return render_template(
        "conferences.html",
        topics=conference_db.get_topics(uid),
        all_topics=conference_db.get_all_topics_with_follow_status(uid),
        grouped_matches=conference_db.get_grouped_matches(uid),
        fetch_status=_conference_fetch_status,
    )


def _start_conference_match_job(user_id, force=False, result_extra=None):
    if _conference_fetch_status["running"]:
        return False
    _conference_fetch_status["running"] = True
    result_extra = result_extra or {}

    def _run():
        try:
            result = match_conferences_for_user(user_id, force=force)
            _conference_fetch_status["last_result"] = {"status": "ok", "match": result, **result_extra}
        except Exception as exc:
            logging.exception("Conference topic matching failed")
            _conference_fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        finally:
            _conference_fetch_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return True


@app.route("/conferences/topics", methods=["POST"])
@login_required
def update_conference_topics():
    uid = g.current_user["id"]
    name = request.form.get("topic", "").strip()
    if name:
        conference_db.create_topic(name, user_id=uid, follow=True)
        _start_conference_match_job(uid, result_extra={"topic_added": True})
    return redirect(url_for("conferences"))


@app.route("/conferences/topics/<int:topic_id>/follow", methods=["POST"])
@login_required
def toggle_conference_topic_follow(topic_id):
    uid = g.current_user["id"]
    action = request.form.get("action", "follow")
    if action == "unfollow":
        conference_db.unfollow_topic(uid, topic_id)
    else:
        conference_db.follow_topic(uid, topic_id)
        _start_conference_match_job(uid, result_extra={"topic_followed": True})
    return redirect(url_for("conferences"))


@app.route("/conferences/topics/<int:topic_id>/delete", methods=["POST"])
@login_required
def delete_conference_topic(topic_id):
    if g.current_user["email"] != ADMIN_EMAIL:
        return redirect(url_for("conferences"))
    conference_db.delete_topic(topic_id)
    return redirect(url_for("conferences"))


@app.route("/conferences/fetch", methods=["POST"])
@login_required
def fetch_conferences():
    if _conference_fetch_status["running"]:
        return jsonify({"ok": False, "error": "A conference refresh is already running"}), 409
    uid = g.current_user["id"]
    _conference_fetch_status["running"] = True

    def _run():
        try:
            result = refresh_for_user(uid)
            _conference_fetch_status["last_result"] = {"status": "ok", **result}
        except Exception as exc:
            logging.exception("Conference refresh failed")
            _conference_fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        finally:
            _conference_fetch_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/conferences/match", methods=["POST"])
@login_required
def match_conferences_now():
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"ok": False, "error": "Not authorised."}), 403
    started = _start_conference_match_job(g.current_user["id"], force=True, result_extra={"manual_match": True})
    if not started:
        return jsonify({"ok": False, "error": "A conference task is already running"}), 409
    return jsonify({"ok": True})


@app.route("/conferences/fetch/status")
@login_required
def conference_fetch_status():
    return jsonify(_conference_fetch_status)

@app.route("/sources/detect", methods=["POST"])
def detect_source():
    from fetchers.detect import detect_source as _detect
    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "").strip()
    if not raw_url:
        return jsonify({"ok": False, "error": "No URL provided"}), 400
    result = _detect(raw_url)
    return jsonify(result)


@app.route("/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(source_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    db.delete_source(source_id)
    return redirect(url_for("sources"))


@app.route("/scheduler/status")
def scheduler_status():
    job = _scheduler.get_job("daily_fetch")
    next_run = None
    if job and job.next_run_time:
        next_run = job.next_run_time.astimezone(_SGT).strftime("%Y-%m-%d %H:%M SGT")
    return jsonify({"running": _scheduler.running, "next_nitter_fetch": next_run})


# ---------------------------------------------------------------------------
# Fetch trigger (AJAX)
# ---------------------------------------------------------------------------

@app.route("/fetch", methods=["POST"])
@login_required
def fetch():
    if _fetch_status["running"]:
        return jsonify({"status": "already_running"}), 409

    data = request.get_json(silent=True) or {}
    date_from = data.get("date_from") or None
    date_to = data.get("date_to") or None
    source_filter_submitted = bool(data.get("source_filter"))
    topic_filter_submitted = bool(data.get("topic_filter"))
    source_ids = data.get("source_ids") if source_filter_submitted else None
    topic_ids = data.get("topic_ids") if topic_filter_submitted else None

    def _run():
        _fetch_status["running"] = True
        log_id = db.log_fetch_start(trigger="manual")
        try:
            source_result = {"total_new": 0, "sources": []} if source_filter_submitted and not source_ids else run_fetch_and_summarize(
                summarize=False,
                date_from=date_from,
                date_to=date_to,
                source_ids=source_ids,
            )
            topic_result = {"total_new": 0, "sources": []} if topic_filter_submitted and not topic_ids else run_topic_fetch(
                topic_ids=topic_ids,
                date_from=date_from,
                date_to=date_to,
            )
            result = {
                "total_new": source_result["total_new"] + topic_result["total_new"],
                "sources": [*source_result["sources"], *topic_result["sources"]],
            }
            db.log_fetch_finish(log_id, result)
            _fetch_status["last_result"] = {"status": "ok", "new_articles": result["total_new"], "sources": result["sources"]}
        except Exception as exc:
            logging.exception("Fetch error")
            db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
            _fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        finally:
            _fetch_status["running"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/fetch/status")
def fetch_status():
    return jsonify(_fetch_status)


@app.route("/topics/fetch/status")
def topic_fetch_status():
    return jsonify(_topic_fetch_status)


@app.route("/sources/<int:source_id>/fetch", methods=["POST"])
@login_required
def fetch_nitter_source(source_id):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"ok": False, "error": "Admin only"}), 403

    source = db.get_source_by_id(source_id)
    if not source or source["type"] != "nitter":
        return jsonify({"ok": False, "error": "Not a Nitter source"}), 400

    if _fetch_status["running"]:
        return jsonify({"ok": False, "error": "A fetch is already running"}), 409

    def _run():
        _fetch_status["running"] = True
        log_id = db.log_fetch_start(trigger="manual-nitter")
        logging.info("[nitter-manual] Fetching source_id=%d (%s)", source_id, source["name"])
        try:
            result = run_fetch_and_summarize(source_ids=[source_id])
            db.log_fetch_finish(log_id, result)
            _fetch_status["last_result"] = {"status": "ok", "new_articles": result["total_new"], "sources": result["sources"]}
        except Exception as exc:
            logging.exception("Nitter manual fetch error")
            db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
            _fetch_status["last_result"] = {"status": "error", "message": str(exc)}
        finally:
            _fetch_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/topics/<int:topic_id>/fetch", methods=["POST"])
@login_required
def fetch_topic(topic_id: int):
    data = request.get_json(silent=True) or {}
    date_from = data.get("date_from") or None
    date_to = data.get("date_to") or None
    topic = db.get_topic_by_id(topic_id)
    if not topic:
        return jsonify({"ok": False, "error": "Topic not found"}), 404
    date_from = date_from or topic.get("backfill_date_from")
    date_to = date_to or topic.get("backfill_date_to")

    started = _start_topic_fetch_job(
        topic_id,
        date_from=date_from,
        date_to=date_to,
        trigger="manual-topic",
    )
    if not started:
        return jsonify({"ok": False, "error": "A topic fetch is already running"}), 409
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Article detail + on-demand summarization
# ---------------------------------------------------------------------------

@app.route("/articles/<int:article_id>")
def article_detail(article_id: int):
    row = db.get_article_by_id(article_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


@app.route("/articles/<int:article_id>/delete", methods=["POST"])
@login_required
def delete_article(article_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    db.delete_article(article_id)
    return jsonify({"ok": True})


@app.route("/topic-items/<int:topic_item_id>/delete", methods=["POST"])
@login_required
def delete_topic_item(topic_item_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    db.delete_topic_item(topic_item_id)
    return jsonify({"ok": True})


@app.route("/articles/<int:article_id>/summarize", methods=["POST"])
@login_required
def article_summarize(article_id: int):
    row = db.get_article_by_id(article_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    # Return cached summary if already done
    if row["summary"]:
        return jsonify({"summary": row["summary"]})
    summary = summarize_single_article(article_id)
    return jsonify({"summary": summary})


@app.route("/articles/<int:article_id>/translate", methods=["POST"])
@login_required
def article_translate(article_id: int):
    row = db.get_article_by_id(article_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    row = dict(row)
    # Return cached translation immediately
    if row.get("translated_content"):
        return jsonify({"status": "done", "content": row["translated_content"]})
    if not row.get("content"):
        return jsonify({"error": "Article has no content to translate."}), 400

    job_id = str(uuid.uuid4())
    _article_translation_jobs[job_id] = {"status": "running", "content": None}
    original = row["content"]

    def _run():
        try:
            from article_translator import translate_article_bilingual
            result = translate_article_bilingual(original)
            db.update_article_translation(article_id, result)
            _article_translation_jobs[job_id] = {"status": "done", "content": result}
        except Exception as exc:
            logging.exception("Article translation failed")
            _article_translation_jobs[job_id] = {"status": "error", "error": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/articles/translate/status/<job_id>")
@login_required
def article_translate_status(job_id: str):
    job = _article_translation_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


def _inline_md_to_html(text: str) -> str:
    """Convert inline markdown to HTML (images, links, bold, italic)."""
    import re as _re
    from html import escape as _esc
    placeholders: dict[str, str] = {}
    idx = 0

    def _ph(val: str) -> str:
        nonlocal idx
        key = f"\x00PH{idx}\x00"
        placeholders[key] = val
        idx += 1
        return key

    # Images first (before links, so they aren't consumed by link regex)
    text = _re.sub(
        r'!\[([^\]]*)\]\(([^)]+)\)',
        lambda m: _ph(f'<img src="{m.group(2)}" alt="{_esc(m.group(1))}" '
                      f'style="max-width:100%;height:auto;display:block;margin:6pt 0">'),
        text,
    )
    # Links
    text = _re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: _ph(f'<a href="{m.group(2)}">{_esc(m.group(1))}</a>'),
        text,
    )
    text = _esc(text)
    text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


def _compress_pdf(pdf_bytes: bytes) -> bytes:
    """Recompress a Playwright-generated PDF using pikepdf object-stream compression.

    Playwright/Chromium PDFs use basic stream compression. pikepdf rewrites them
    with cross-reference object streams and better Flate settings, typically
    reducing file size by 30–50% with no visual quality change.
    Falls back to the original bytes if pikepdf is unavailable or fails.
    """
    try:
        import io
        import pikepdf
        src = io.BytesIO(pdf_bytes)
        dst = io.BytesIO()
        with pikepdf.open(src) as pdf:
            pdf.save(
                dst,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                recompress_flate=True,
                deterministic_id=False,
            )
        compressed = dst.getvalue()
        # Safety check: never return something larger than the original
        return compressed if len(compressed) < len(pdf_bytes) else pdf_bytes
    except Exception:
        return pdf_bytes  # graceful fallback — original PDF is still valid


def _md_to_html_for_pdf(md: str) -> str:
    """Convert markdown article content to clean HTML for PDF rendering."""
    import re as _re
    lines = md.split("\n")
    out: list[str] = []
    in_code = False
    in_bq = False
    in_ul = False

    def close_bq():
        nonlocal in_bq
        if in_bq:
            out.append("</blockquote>")
            in_bq = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for line in lines:
        # Code fences
        if line.startswith("```"):
            close_bq(); close_ul()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            from html import escape as _esc
            out.append(_esc(line))
            continue

        # Headings
        if line.startswith("### "):
            close_bq(); close_ul()
            out.append(f"<h3>{_inline_md_to_html(line[4:])}</h3>")
        elif line.startswith("## "):
            close_bq(); close_ul()
            out.append(f"<h2>{_inline_md_to_html(line[3:])}</h2>")
        elif line.startswith("# "):
            close_bq(); close_ul()
            out.append(f"<h1>{_inline_md_to_html(line[2:])}</h1>")
        # Blockquotes (Chinese translations)
        elif line.startswith("> "):
            close_ul()
            if not in_bq:
                out.append('<blockquote class="zh">')
                in_bq = True
            out.append(f"<p>{_inline_md_to_html(line[2:])}</p>")
        elif line.strip() == ">":
            if not in_bq:
                out.append('<blockquote class="zh">')
                in_bq = True
            out.append("<p></p>")
        # List items
        elif _re.match(r"^[-*] ", line):
            close_bq()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline_md_to_html(line[2:])}</li>")
        # Horizontal rule
        elif _re.fullmatch(r"[-*_]{3,}", line.strip()):
            close_bq(); close_ul()
            out.append("<hr>")
        # Empty line
        elif not line.strip():
            close_bq(); close_ul()
        # Normal paragraph
        else:
            close_bq(); close_ul()
            out.append(f"<p>{_inline_md_to_html(line)}</p>")

    close_bq(); close_ul()
    return "\n".join(out)



def _markdown_pdf_bytes(title: str, content: str, version_label: str, source: str = "", pub_date: str = "", url: str = "") -> bytes:
    """Render article-style Markdown content to PDF bytes."""
    from html import escape
    from playwright.sync_api import sync_playwright

    body_html = _md_to_html_for_pdf(content) if content else "<p>（暂无内容）</p>"
    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 2.5cm; }}
  body {{
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "STSong", "SimSun",
                 "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", Georgia, serif;
    font-size: 10.5pt; line-height: 1.85; color: #1a1a1a;
  }}
  h1 {{ font-size: 14pt; font-weight: bold; margin: 0 0 8pt 0; line-height: 1.4; }}
  h2 {{ font-size: 12pt; font-weight: bold; margin: 16pt 0 6pt 0;
        padding-left: 8pt; border-left: 3pt solid #444; color: #222; }}
  h3 {{ font-size: 11pt; font-weight: bold; margin: 12pt 0 5pt 0; }}
  .meta {{ font-size: 8.5pt; color: #666; margin-bottom: 18pt; }}
  .meta a {{ color: #555; text-decoration: none; }}
  p {{ margin: 0 0 8pt 0; }}
  ul {{ margin: 0 0 8pt 1.5em; padding: 0; }}
  li {{ margin-bottom: 4pt; }}
  blockquote.zh {{
    margin: 2pt 0 10pt 0; padding: 6pt 12pt;
    border-left: 3pt solid #2563eb;
    color: #1e40af;
    font-style: normal;
  }}
  blockquote.zh p {{ margin: 0; color: #1e40af; }}
  pre {{ background: #f5f5f5; padding: 8pt; font-size: 9pt; overflow: hidden; }}
  code {{ font-family: monospace; }}
  img {{ max-width: 100%; height: auto; display: block; margin: 6pt 0; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 14pt 0; }}
  a {{ color: #2563eb; }}
</style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <div class="meta">
    {f'<span>{escape(source)}</span>' if source else ''}
    {f' &nbsp;·&nbsp; <span>{pub_date}</span>' if pub_date else ''}
    {f'<br><a href="{escape(url)}">{escape(url)}</a>' if url else ''}
    &nbsp;·&nbsp; <span>{escape(version_label)}</span>
  </div>
  {body_html}
</body>
</html>"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        pdf_bytes = page.pdf(
            format="A4",
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            print_background=False,
        )
        browser.close()

    return _compress_pdf(pdf_bytes)
@app.route("/articles/<int:article_id>/download/pdf")
@login_required
def article_download_pdf(article_id: int):
    from flask import Response as FlaskResponse
    from html import escape
    from playwright.sync_api import sync_playwright

    row = db.get_article_by_id(article_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    row = dict(row)

    version = request.args.get("version", "original")
    if version == "translated" and row.get("translated_content"):
        content = row["translated_content"]
        version_label = "中英双语"
    else:
        content = row.get("content") or ""
        version_label = "原文"

    title = row.get("title") or row.get("url", "")
    source = row.get("source_name", "")
    pub_date = (row.get("published_at") or row.get("fetched_at") or "")[:10]
    url = row.get("url", "")

    body_html = _md_to_html_for_pdf(content) if content else "<p>（暂无内容）</p>"

    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 2.5cm; }}
  body {{
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "STSong", "SimSun",
                 "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", Georgia, serif;
    font-size: 10.5pt; line-height: 1.85; color: #1a1a1a;
  }}
  h1 {{ font-size: 14pt; font-weight: bold; margin: 0 0 8pt 0; line-height: 1.4; }}
  h2 {{ font-size: 12pt; font-weight: bold; margin: 16pt 0 6pt 0;
        padding-left: 8pt; border-left: 3pt solid #444; color: #222; }}
  h3 {{ font-size: 11pt; font-weight: bold; margin: 12pt 0 5pt 0; }}
  .meta {{ font-size: 8.5pt; color: #666; margin-bottom: 18pt; }}
  .meta a {{ color: #555; text-decoration: none; }}
  p {{ margin: 0 0 8pt 0; }}
  ul {{ margin: 0 0 8pt 1.5em; padding: 0; }}
  li {{ margin-bottom: 4pt; }}
  blockquote.zh {{
    margin: 2pt 0 10pt 0; padding: 6pt 12pt;
    border-left: 3pt solid #2563eb;
    color: #1e40af;
    font-style: normal;
  }}
  blockquote.zh p {{ margin: 0; color: #1e40af; }}
  pre {{ background: #f5f5f5; padding: 8pt; font-size: 9pt; overflow: hidden; }}
  code {{ font-family: monospace; }}
  img {{ max-width: 100%; height: auto; display: block; margin: 6pt 0; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 14pt 0; }}
  a {{ color: #2563eb; }}
</style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <div class="meta">
    {f'<span>{escape(source)}</span>' if source else ''}
    {f' &nbsp;·&nbsp; <span>{pub_date}</span>' if pub_date else ''}
    {f'<br><a href="{escape(url)}">{escape(url)}</a>' if url else ''}
    &nbsp;·&nbsp; <span>{version_label}</span>
  </div>
  {body_html}
</body>
</html>"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        pdf_bytes = page.pdf(
            format="A4",
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            print_background=False,
        )
        browser.close()

    pdf_bytes = _compress_pdf(pdf_bytes)

    safe_title = _safe_filename(title, str(article_id))
    suffix = "bilingual" if version == "translated" else "original"
    filename = f"{safe_title}_{suffix}.pdf"
    return FlaskResponse(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}"},
    )


# ---------------------------------------------------------------------------
# Batch digest (all visible articles → one AI summary)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@app.route("/tools")
@login_required
def tools_page():
    response = app.make_response(render_template("tools.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _pdf_tool_job_dir(job_id: str) -> str:
    return os.path.join(_PDF_TOOL_UPLOAD_DIR, job_id)


def _pdf_tool_image_dir(job_id: str) -> str:
    return os.path.join(_pdf_tool_job_dir(job_id), "images")



def _pdf_tool_meta_path(job_id: str) -> str:
    return os.path.join(_pdf_tool_job_dir(job_id), _PDF_TOOL_META_FILENAME)


def _pdf_tool_output_path(job_id: str) -> str:
    return os.path.join(_pdf_tool_job_dir(job_id), _PDF_TOOL_OUTPUT_FILENAME)


def _pdf_tool_translated_path(job_id: str) -> str:
    return os.path.join(_pdf_tool_job_dir(job_id), _PDF_TOOL_TRANSLATED_FILENAME)


def _is_valid_pdf_tool_job_id(job_id: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{36}", job_id or ""))


def _read_pdf_tool_meta(job_id: str) -> dict | None:
    if not _is_valid_pdf_tool_job_id(job_id):
        return None
    path = _pdf_tool_meta_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        import json
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["job_id"] = job_id
        return data
    except Exception:
        logging.exception("Failed to read PDF tool metadata for %s", job_id)
        return None


def _write_pdf_tool_meta(job_id: str, updates: dict) -> dict:
    os.makedirs(_pdf_tool_job_dir(job_id), exist_ok=True)
    meta = _read_pdf_tool_meta(job_id) or {"job_id": job_id}
    meta.update(updates)
    import json
    with open(_pdf_tool_meta_path(job_id), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def _can_access_pdf_tool_job(meta: dict | None) -> bool:
    if not meta or not g.current_user:
        return False
    if g.current_user["email"] == ADMIN_EMAIL:
        return True
    return int(meta.get("user_id") or 0) == int(g.current_user["id"])


def _pdf_tool_public_job(job_id: str) -> dict | None:
    meta = _read_pdf_tool_meta(job_id)
    memory_job = _pdf_tool_jobs.get(job_id)
    if memory_job:
        meta = dict(meta or {})
        meta.update({
            "job_id": job_id,
            "status": memory_job.get("status"),
            "title": memory_job.get("title"),
            "original_filename": memory_job.get("original_filename"),
            "error": memory_job.get("error"),
        })
    if not _can_access_pdf_tool_job(meta):
        return None
    status = meta.get("status") or "running"
    item = {
        "job_id": job_id,
        "status": status,
        "title": meta.get("title") or meta.get("original_filename") or job_id,
        "original_filename": meta.get("original_filename"),
        "created_at": meta.get("created_at"),
        "completed_at": meta.get("completed_at"),
        "error": meta.get("error"),
    }
    if status == "done" and os.path.exists(_pdf_tool_output_path(job_id)):
        item["download_url"] = url_for("tools_pdf_translate_download", job_id=job_id)
    return item


def _list_pdf_tool_jobs(limit: int = 20) -> list[dict]:
    ids = set(_pdf_tool_jobs.keys())
    if os.path.isdir(_PDF_TOOL_UPLOAD_DIR):
        for name in os.listdir(_PDF_TOOL_UPLOAD_DIR):
            if _is_valid_pdf_tool_job_id(name):
                ids.add(name)
    jobs = [item for item in (_pdf_tool_public_job(job_id) for job_id in ids) if item]
    jobs.sort(key=lambda item: item.get("completed_at") or item.get("created_at") or "", reverse=True)
    return jobs[:limit]
def _localize_pdf_tool_images(md: str, job_id: str) -> str:
    from pathlib import Path

    image_dir = _pdf_tool_image_dir(job_id)

    def repl(match):
        alt = match.group(1)
        filename = secure_filename(match.group(2))
        local_path = os.path.abspath(os.path.join(image_dir, filename))
        image_root = os.path.abspath(image_dir)
        if not local_path.startswith(image_root):
            return match.group(0)
        if not os.path.exists(local_path):
            return match.group(0)
        return f"![{alt}]({Path(local_path).as_uri()})"

    pattern = rf"!\[([^\]]*)\]\(/tools/pdf-image/{re.escape(job_id)}/([^\)]+)\)"
    return re.sub(pattern, repl, md)


@app.route("/tools/pdf-translate", methods=["POST"])
@login_required
def tools_pdf_translate():
    upload = request.files.get("pdf")
    if not upload or not upload.filename:
        return jsonify({"error": "请选择一个 PDF 文件。"}), 400

    original_name = upload.filename
    if os.path.splitext(original_name)[1].lower() != ".pdf":
        return jsonify({"error": "只支持上传 PDF 文件。"}), 400

    job_id = str(uuid.uuid4())
    job_dir = _pdf_tool_job_dir(job_id)
    image_dir = _pdf_tool_image_dir(job_id)
    os.makedirs(image_dir, exist_ok=True)

    safe_upload_name = secure_filename(original_name) or "upload.pdf"
    pdf_path = os.path.join(job_dir, safe_upload_name)
    upload.save(pdf_path)

    title = os.path.splitext(original_name)[0].strip() or "Uploaded PDF"
    created_at = datetime.now(timezone.utc).isoformat()
    _pdf_tool_jobs[job_id] = {
        "status": "running",
        "title": title,
        "original_filename": original_name,
        "original_markdown": "",
        "translated_markdown": "",
        "pdf_bytes": None,
        "error": None,
    }
    _write_pdf_tool_meta(job_id, {
        "status": "running",
        "title": title,
        "original_filename": original_name,
        "created_at": created_at,
        "user_id": g.current_user["id"],
        "initiated_by": g.current_user["email"],
    })

    def _run():
        try:
            from article_translator import translate_pdf_markdown_bilingual
            from pdf_tools import extract_pdf_markdown

            original_md = extract_pdf_markdown(pdf_path, image_dir, job_id)
            if not original_md:
                raise ValueError("没有从 PDF 中抽取到可翻译的文字或图片。")

            _pdf_tool_jobs[job_id].update({
                "status": "translating",
                "original_markdown": original_md,
            })
            _write_pdf_tool_meta(job_id, {"status": "translating"})
            translated_md = translate_pdf_markdown_bilingual(original_md)
            pdf_md = _localize_pdf_tool_images(translated_md, job_id)
            pdf_bytes = _markdown_pdf_bytes(title, pdf_md, "中英双语", source="上传 PDF")
            with open(_pdf_tool_output_path(job_id), "wb") as fh:
                fh.write(pdf_bytes)
            with open(_pdf_tool_translated_path(job_id), "w", encoding="utf-8") as fh:
                fh.write(translated_md)
            _pdf_tool_jobs[job_id].update({
                "status": "done",
                "translated_markdown": translated_md,
                "pdf_bytes": pdf_bytes,
            })
            _write_pdf_tool_meta(job_id, {
                "status": "done",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logging.exception("PDF translation tool failed")
            _pdf_tool_jobs[job_id].update({"status": "error", "error": str(exc)})
            _write_pdf_tool_meta(job_id, {
                "status": "error",
                "error": str(exc),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/tools/pdf-translate/jobs")
@login_required
def tools_pdf_translate_jobs():
    return jsonify({"jobs": _list_pdf_tool_jobs()})


@app.route("/tools/pdf-translate/status/<job_id>")
@login_required
def tools_pdf_translate_status(job_id: str):
    job = _pdf_tool_jobs.get(job_id)
    meta = _read_pdf_tool_meta(job_id)
    if not job and not meta:
        return jsonify({"error": "Job not found."}), 404
    if not _can_access_pdf_tool_job(meta):
        return jsonify({"error": "Job not found."}), 404

    status = (job or meta).get("status", "running")
    result = {
        "status": status,
        "title": (job or meta).get("title"),
        "error": (job or meta).get("error"),
    }
    if status == "done":
        translated_md = (job or {}).get("translated_markdown") or ""
        if not translated_md and os.path.exists(_pdf_tool_translated_path(job_id)):
            with open(_pdf_tool_translated_path(job_id), "r", encoding="utf-8") as fh:
                translated_md = fh.read()
        result.update({
            "download_url": url_for("tools_pdf_translate_download", job_id=job_id),
            "preview_markdown": translated_md,
        })
    elif job and job.get("original_markdown"):
        result["preview_markdown"] = job["original_markdown"][:12000]
    return jsonify(result)


@app.route("/tools/pdf-translate/download/<job_id>")
@login_required
def tools_pdf_translate_download(job_id: str):
    from flask import Response as FlaskResponse

    job = _pdf_tool_jobs.get(job_id)
    meta = _read_pdf_tool_meta(job_id)
    if not job and not meta:
        return jsonify({"error": "Job not found."}), 404
    if not _can_access_pdf_tool_job(meta):
        return jsonify({"error": "Job not found."}), 404

    pdf_bytes = (job or {}).get("pdf_bytes")
    if not pdf_bytes and os.path.exists(_pdf_tool_output_path(job_id)):
        with open(_pdf_tool_output_path(job_id), "rb") as fh:
            pdf_bytes = fh.read()
    if (job or meta).get("status") != "done" or not pdf_bytes:
        return jsonify({"error": "PDF is not ready yet."}), 400

    title = (job or meta).get("title")
    filename = f"{_safe_filename(title, job_id)}_bilingual.pdf"
    return FlaskResponse(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}"},
    )


@app.route("/tools/pdf-image/<job_id>/<path:filename>")
@login_required
def tools_pdf_image(job_id: str, filename: str):
    safe_name = secure_filename(os.path.basename(filename))
    image_dir = os.path.abspath(_pdf_tool_image_dir(job_id))
    image_path = os.path.abspath(os.path.join(image_dir, safe_name))
    if not image_path.startswith(image_dir) or not os.path.exists(image_path):
        return jsonify({"error": "Image not found."}), 404
    return send_file(image_path)
@app.route("/digest/generate", methods=["POST"])
@login_required
def digest_generate():
    data = request.get_json(force=True, silent=True) or {}
    article_ids = data.get("article_ids", [])
    uid = g.current_user["id"] if g.current_user else None

    # Preset-based generation: look up source_ids then query articles
    preset_id = data.get("preset_id")
    if preset_id:
        preset = db.get_digest_preset(int(preset_id), uid)
        if not preset:
            return jsonify({"error": "Preset not found"}), 404
        source_ids = preset["source_ids"] or None
        date_from = data.get("date_from")
        date_to = data.get("date_to")
        articles = db.get_articles(date_from=date_from, date_to=date_to, source_ids=source_ids)
        article_ids = [a["id"] for a in articles]

    if not article_ids:
        return jsonify({"error": "No article IDs provided"}), 400

    job_id = str(uuid.uuid4())
    _digest_jobs[job_id] = {"status": "running", "result": None}

    def _run():
        try:
            result = generate_batch_digest(article_ids, user_id=uid)
            _digest_jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            logging.exception("Digest generation failed")
            _digest_jobs[job_id] = {"status": "error", "result": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


# ---------------------------------------------------------------------------
# Digest preset CRUD
# ---------------------------------------------------------------------------

@app.route("/digest/presets", methods=["GET"])
@login_required
def digest_presets_list():
    return jsonify(db.get_digest_presets(g.current_user["id"]))


@app.route("/digest/presets", methods=["POST"])
@login_required
def digest_presets_create():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    source_ids = [int(x) for x in data.get("source_ids", [])]
    if not name:
        return jsonify({"error": "Name required"}), 400
    uid = g.current_user["id"]
    preset = db.create_digest_preset(uid, name, source_ids)
    if preset is None:
        return jsonify({"error": "最多只能创建 2 个简报配置"}), 400
    _sync_presets_to_follows(uid)
    return jsonify(preset), 201


@app.route("/digest/presets/<int:preset_id>", methods=["PUT"])
@login_required
def digest_presets_update(preset_id: int):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    source_ids = [int(x) for x in data.get("source_ids", [])]
    if not name:
        return jsonify({"error": "Name required"}), 400
    digest_enabled = int(bool(data.get("digest_enabled", False)))
    digest_frequency_days = int(data.get("digest_frequency_days", 7))
    uid = g.current_user["id"]
    db.update_digest_preset(preset_id, uid, name, source_ids,
                            digest_enabled=digest_enabled, digest_frequency_days=digest_frequency_days)
    _sync_presets_to_follows(uid)
    return jsonify({"ok": True})


@app.route("/digest/presets/<int:preset_id>", methods=["DELETE"])
@login_required
def digest_presets_delete(preset_id: int):
    db.delete_digest_preset(preset_id, g.current_user["id"])
    return jsonify({"ok": True})



@app.route("/raw-feed/subscription", methods=["GET"])
@login_required
def raw_feed_subscription_get():
    return jsonify(db.get_raw_feed_subscription(g.current_user["id"]))


@app.route("/raw-feed/subscription", methods=["PUT"])
@login_required
def raw_feed_subscription_update():
    data = request.get_json(force=True, silent=True) or {}
    topic_ids = [int(x) for x in data.get("topic_ids", [])]
    source_ids = [int(x) for x in data.get("source_ids", [])]
    enabled = bool(data.get("enabled", False))
    try:
        frequency_days = int(data.get("frequency_days", 1))
    except (TypeError, ValueError):
        frequency_days = 1
    if frequency_days not in (1, 3, 7, 14):
        frequency_days = 1
    uid = g.current_user["id"]
    sub = db.update_raw_feed_subscription(uid, topic_ids, enabled, frequency_days, source_ids=source_ids)
    _sync_raw_feed_to_topic_follows(uid, topic_ids)
    return jsonify(sub)

@app.route("/digest/status/<job_id>")
@login_required
def digest_job_status(job_id: str):
    job = _digest_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------------
# Digest export
# ---------------------------------------------------------------------------

@app.route("/digest")
def digest():
    today = date.today().isoformat()
    date_from = request.args.get("date_from", today)
    date_to = request.args.get("date_to", today)
    md = build_email_digest(date_from=date_from, date_to=date_to)
    return app.response_class(md, mimetype="text/plain; charset=utf-8")


@app.route("/digests")
@login_required
def digests_history():
    digests = db.get_all_digests_with_meta()
    for d in digests:
        d["created_at_sgt"] = to_sgt_filter(d["created_at"])
    return render_template("digests.html", digests=digests)


# ---------------------------------------------------------------------------
# Activity log viewer
# ---------------------------------------------------------------------------

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")
LOG_TAIL_LINES = 200


def _list_log_files() -> list[str]:
    """Return all app.log* paths in logs/ sorted oldest-first."""
    import glob as _glob
    files = _glob.glob(os.path.join(LOG_DIR, "app.log*"))

    def _sort_key(p):
        name = os.path.basename(p)
        # rotated suffix is YYYY-MM-DD; current file sorts last
        return name[len("app.log."):] if "." in name[len("app.log"):] else "9999-99-99"

    return sorted(files, key=_sort_key)


def _read_tail_lines(n: int) -> list[str]:
    """Last n lines across all log files (oldest file first)."""
    all_lines: list[str] = []
    for fpath in _list_log_files():
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                all_lines.extend(f.readlines())
        except FileNotFoundError:
            pass
    return all_lines[-n:]


@app.route("/logs/download")
@login_required
def logs_download():
    filename = request.args.get("file", "app.log")
    # Prevent path traversal — basename only, must start with "app.log"
    if os.sep in filename or "/" in filename or not filename.startswith("app.log"):
        return "Invalid log file.", 400
    fpath = os.path.join(LOG_DIR, filename)
    if not os.path.exists(fpath):
        return "Log file not found.", 404
    return send_file(
        os.path.abspath(fpath),
        mimetype="text/plain",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/logs")
@login_required
def logs_view():
    fetch_log = [dict(r) for r in db.get_fetch_log(limit=20)]
    import json as _json
    for entry in fetch_log:
        try:
            entry["sources_parsed"] = _json.loads(entry["sources_json"] or "[]")
        except Exception:
            entry["sources_parsed"] = []

    raw_lines = _read_tail_lines(LOG_TAIL_LINES)
    # Newest file first for the download selector
    log_files = [os.path.basename(f) for f in reversed(_list_log_files())]

    return render_template("logs.html", fetch_log=fetch_log, raw_lines=raw_lines, log_files=log_files)


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

@app.route("/subscribe")
@login_required
def subscribe():
    uid = g.current_user["id"]
    digest_presets = db.get_digest_presets(uid)
    raw_feed_subscription = db.get_raw_feed_subscription(uid)
    all_sources = [dict(s) for s in db.get_all_sources()]
    all_topics = db.get_all_topics(active_only=True)
    return render_template(
        "subscribe.html",
        digest_presets=digest_presets,
        raw_feed_subscription=raw_feed_subscription,
        all_sources=all_sources,
        all_topics=all_topics,
    )

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    success = False
    if request.method == "POST":
        enabled = bool(request.form.get("digest_enabled"))
        try:
            freq = int(request.form.get("digest_frequency_days", 7))
        except ValueError:
            freq = 7
        if freq not in (1, 3, 7, 14):
            freq = 7
        db.update_user_digest_settings(g.current_user["id"], enabled, freq)
        g.current_user = db.get_user_by_id(g.current_user["id"])
        success = True
    token_summary = db.get_token_usage_summary(user_id=g.current_user["id"])
    browser_summary = db.get_token_usage_summary()  # global - includes browser_agent
    browser_rows = [r for r in browser_summary if r["operation"] == "browser_agent"]
    is_admin = g.current_user["email"] == ADMIN_EMAIL
    all_users = db.get_all_users() if is_admin else []
    weekly_tokens = db.get_token_usage_by_user_week() if is_admin else []
    user_follows = {}
    admin_digest_rows = []
    admin_raw_feed_rows = []
    if is_admin:
        for u in all_users:
            user_follows[u["id"]] = db.get_all_sources_with_follow_status(u["id"])
        all_preset_list = db.get_digest_presets_for_users([u["id"] for u in all_users])
        presets_by_user = {}
        for p in all_preset_list:
            presets_by_user.setdefault(p["user_id"], []).append(p)
        raw_subs_by_user = {
            s["user_id"]: s
            for s in db.get_raw_feed_subscriptions_for_users([u["id"] for u in all_users])
        }
        for u in all_users:
            raw_sub = raw_subs_by_user.get(u["id"]) or db.get_raw_feed_subscription(u["id"])
            admin_raw_feed_rows.append({
                "user_id": u["id"],
                "email": u["email"],
                "enabled": raw_sub["enabled"],
                "frequency_days": raw_sub["frequency_days"],
                "last_sent": raw_sub.get("last_sent"),
                "topic_count": len(raw_sub.get("topic_ids") or []),
                "source_count": len(raw_sub.get("source_ids") or []),
            })
        for u in all_users:
            user_presets = presets_by_user.get(u["id"], [])
            if not user_presets:
                admin_digest_rows.append({
                    "user_id": u["id"],
                    "email": u["email"],
                    "preset_id": None,
                    "preset_name": "—",
                    "digest_enabled": False,
                    "digest_frequency_days": 7,
                    "digest_last_sent": None,
                })
            else:
                for p in user_presets:
                    admin_digest_rows.append({
                        "user_id": u["id"],
                        "email": u["email"],
                        "preset_id": p["id"],
                        "preset_name": p["name"],
                        "digest_enabled": p["digest_enabled"],
                        "digest_frequency_days": p["digest_frequency_days"],
                        "digest_last_sent": p.get("digest_last_sent"),
                    })
    return render_template(
        "settings.html",
        user=g.current_user,
        success=success,
        token_summary=token_summary,
        browser_rows=browser_rows,
        is_admin=is_admin,
        all_users=all_users,
        weekly_tokens=weekly_tokens,
        user_follows=user_follows,
        admin_digest_rows=admin_digest_rows,
        admin_raw_feed_rows=admin_raw_feed_rows,
        send_started=request.args.get("send_started"),
    )

@app.route("/admin/users/<int:user_id>/follows", methods=["POST"])
@login_required
def admin_update_user_follows(user_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    checked_ids = [int(v) for v in request.form.getlist("source_ids")]
    db.set_user_follows(user_id, checked_ids)
    _sync_follows_to_all_presets(user_id, checked_ids)
    return redirect(url_for("settings") + f"#follows-{user_id}")


@app.route("/admin/presets/<int:preset_id>/send", methods=["POST"])
@login_required
def admin_send_preset_digest(preset_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    threading.Thread(target=_send_preset_digest_now, args=(preset_id,), daemon=True).start()
    return redirect(url_for("settings") + "?send_started=ai")


@app.route("/admin/raw-feed/<int:user_id>/send", methods=["POST"])
@login_required
def admin_send_raw_feed(user_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    threading.Thread(target=_send_raw_feed_now, args=(user_id,), daemon=True).start()
    return redirect(url_for("settings") + "?send_started=raw")

@app.route("/admin/presets/<int:preset_id>/digest", methods=["POST"])
@login_required
def admin_update_preset_digest(preset_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    enabled = request.form.get("digest_enabled") == "1"
    try:
        freq = int(request.form.get("digest_frequency_days", 7))
    except ValueError:
        freq = 7
    if freq not in (1, 3, 7, 14):
        freq = 7
    db.update_preset_email_settings(preset_id, enabled, freq)
    return redirect(url_for("settings"))


# ---------------------------------------------------------------------------
# YouTube transcript
# ---------------------------------------------------------------------------

@app.route("/transcript")
@login_required
def transcript_page():
    rows = db.list_transcript_jobs(limit=60)
    sidebar_jobs = [
        {
            "job_id":       r["job_id"],
            "video_id":     r["video_id"],
            "video_url":    r["video_url"],
            "video_title":  r["video_title"],
            "video_author": r["video_author"],
            "mode":         r["mode"],
            "status":       r["status"],
            "initiated_by": r["initiated_by"],
            "input_type":   r["input_type"],
            "original_filename": r["original_filename"],
            "created_at":   r["created_at"],
        }
        for r in rows
    ]
    is_admin = g.current_user["email"] == ADMIN_EMAIL
    return render_template("transcript.html", sidebar_jobs=sidebar_jobs, is_admin=is_admin)


@app.route("/transcript/jobs")
@login_required
def transcript_jobs_list():
    """AJAX endpoint for sidebar refresh."""
    jobs = db.list_transcript_jobs(limit=60)
    return jsonify([
        {
            "job_id":       j["job_id"],
            "video_id":     j["video_id"],
            "video_url":    j["video_url"],
            "video_title":  j["video_title"],
            "video_author": j["video_author"],
            "mode":         j["mode"],
            "status":       j["status"],
            "initiated_by": j["initiated_by"],
            "input_type":   j["input_type"],
            "original_filename": j["original_filename"],
            "created_at":   j["created_at"],
        }
        for j in jobs
    ])



def _is_allowed_transcript_upload(filename: str, mimetype: str | None) -> bool:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _ALLOWED_TRANSCRIPT_UPLOAD_EXTENSIONS:
        return True
    return bool(mimetype and (mimetype.startswith("audio/") or mimetype.startswith("video/")))


def _save_transcript_upload(file_storage, job_id: str) -> tuple[str, str]:
    original_name = secure_filename(file_storage.filename or "") or f"upload-{job_id}"
    ext = os.path.splitext(original_name)[1].lower()
    os.makedirs(_TRANSCRIPT_UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(_TRANSCRIPT_UPLOAD_DIR, f"{job_id}{ext}")
    file_storage.save(dest)
    return dest, original_name


def _delete_transcript_media_file(job) -> None:
    if not job:
        return
    media_path = (job["audio_path"] or "").strip()
    if media_path and os.path.isfile(media_path):
        try:
            os.remove(media_path)
        except OSError:
            pass


def _create_pasted_transcript_job(transcript, title):
    from transcript_worker import translate_transcript

    transcript = (transcript or "").strip()
    title = (title or "").strip()[:200]

    if not transcript:
        return None, "Please paste a transcript first."
    if len(transcript) < 20:
        return None, "Transcript is too short to process."

    job_id = db.create_transcript_job(
        video_url="",
        video_id=f"paste:{uuid.uuid4().hex}",
        mode="no_diarization",
        initiated_by=g.current_user["email"],
        input_type="paste",
        original_filename=None,
    )
    db.set_transcript_metadata(job_id, video_title=title or "Pasted transcript", video_author=None)
    db.update_transcript_job(job_id, status="translating", transcript=transcript)

    threading.Thread(target=translate_transcript, args=(job_id,), daemon=True).start()
    return job_id, None


def _paste_chunk_path(upload_id, index):
    return os.path.join(_TRANSCRIPT_PASTE_CHUNK_DIR, upload_id, f"{index:04d}.txt")


@app.route("/transcript/process", methods=["POST"])
@login_required
def transcript_process():
    from transcript_worker import (
        normalize_url,
        extract_video_id, is_youtube_url,
        extract_xiaoyuzhou_episode_id, is_xiaoyuzhou_url,
        extract_bilibili_video_id, is_bilibili_url,
        process_transcript_job,
    )

    data   = request.get_json(silent=True) or {}
    url    = normalize_url((data.get("url") or "").strip())
    mode   = (data.get("mode") or "no_diarization").strip()

    if mode not in ("no_diarization", "diarization"):
        return jsonify({"error": "Invalid mode."}), 400
    if not url:
        return jsonify({"error": "URL is required."}), 400

    if is_youtube_url(url):
        video_id = extract_video_id(url)
    elif is_bilibili_url(url):
        video_id = extract_bilibili_video_id(url)
    elif is_xiaoyuzhou_url(url):
        video_id = extract_xiaoyuzhou_episode_id(url)
    else:
        return jsonify({"error": "请输入 YouTube 视频、Bilibili 视频或小宇宙播客单集链接。"}), 400

    # Return cached result if a completed job already exists for this video+mode
    cached = db.get_done_transcript_job(video_id, mode)
    if cached:
        return jsonify({"job_id": cached["job_id"], "cached": True})

    job_id = db.create_transcript_job(
        video_url=url, video_id=video_id, mode=mode,
        initiated_by=g.current_user["email"],
    )

    thread = threading.Thread(
        target=process_transcript_job,
        args=(job_id, url, video_id, mode),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "cached": False})


@app.route("/transcript/upload", methods=["POST"])
@login_required
def transcript_upload():
    from transcript_worker import process_uploaded_transcript_job

    mode = (request.form.get("mode") or "no_diarization").strip()
    media = request.files.get("media")

    if mode not in ("no_diarization", "diarization"):
        return jsonify({"error": "Invalid mode."}), 400
    if not media or not (media.filename or "").strip():
        return jsonify({"error": "Please choose an audio or video file."}), 400
    if not _is_allowed_transcript_upload(media.filename or "", getattr(media, "mimetype", None)):
        return jsonify({"error": "Unsupported file type. Please upload audio or video media."}), 400

    job_id = db.create_transcript_job(
        video_url="",
        video_id=f"upload:{uuid.uuid4().hex}",
        mode=mode,
        initiated_by=g.current_user["email"],
        input_type="upload",
        original_filename=secure_filename(media.filename or "") or None,
    )

    try:
        media_path, original_name = _save_transcript_upload(media, job_id)
        db.update_transcript_job(job_id, status="pending", audio_path=media_path)
    except Exception:
        db.delete_transcript_job(job_id)
        raise

    thread = threading.Thread(
        target=process_uploaded_transcript_job,
        args=(job_id, media_path, original_name, mode),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "cached": False})


@app.route("/transcript/paste", methods=["POST"])
@login_required
def transcript_paste():
    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    title = (data.get("title") or "").strip()[:200]

    job_id, error = _create_pasted_transcript_job(transcript, title)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"job_id": job_id, "cached": False})


@app.route("/transcript/paste-chunk", methods=["POST"])
@login_required
def transcript_paste_chunk():
    data = request.get_json(silent=True) or {}
    upload_id = (data.get("upload_id") or "").strip()
    title = (data.get("title") or "").strip()[:200]
    chunk = data.get("chunk") or ""

    try:
        index = int(data.get("index"))
        total = int(data.get("total"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid chunk metadata."}), 400

    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        return jsonify({"error": "Invalid upload id."}), 400
    if total < 1 or total > _TRANSCRIPT_PASTE_CHUNK_MAX_PARTS or index < 0 or index >= total:
        return jsonify({"error": "Invalid chunk range."}), 400
    if len(chunk) > _TRANSCRIPT_PASTE_CHUNK_MAX_CHARS:
        return jsonify({"error": "Chunk is too large."}), 413

    upload_dir = os.path.join(_TRANSCRIPT_PASTE_CHUNK_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    chunk_path = _paste_chunk_path(upload_id, index)
    with open(chunk_path, "w", encoding="utf-8") as fh:
        fh.write(chunk)

    _paste_logger.info(
        "json_chunk upload=%s index=%s/%s chars=%s remote=%s user=%s",
        upload_id[-8:],
        index + 1,
        total,
        len(chunk),
        request.headers.get("X-Forwarded-For") or request.remote_addr or "-",
        g.current_user["email"],
    )

    if index != total - 1:
        return jsonify({"received": index, "done": False})

    missing = [i for i in range(total) if not os.path.isfile(_paste_chunk_path(upload_id, i))]
    if missing:
        return jsonify({"error": "Some chunks are missing. Please submit again."}), 400

    try:
        parts = []
        for i in range(total):
            with open(_paste_chunk_path(upload_id, i), "r", encoding="utf-8") as fh:
                parts.append(fh.read())
        transcript = "".join(parts)
        _paste_logger.info(
            "json_upload_complete upload=%s chunks=%s chars=%s",
            upload_id[-8:],
            total,
            len(transcript),
        )
        job_id, error = _create_pasted_transcript_job(transcript, title)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"job_id": job_id, "cached": False, "done": True})
    finally:
        import shutil
        shutil.rmtree(upload_dir, ignore_errors=True)


@app.route("/transcript/paste-header-chunk", methods=["GET"])
@login_required
def transcript_paste_header_chunk():
    upload_id = (request.headers.get("X-Paste-Upload-Id") or "").strip()
    title_b64 = request.headers.get("X-Paste-Title") or ""
    chunk_b64 = request.headers.get("X-Paste-Chunk") or ""

    try:
        index = int(request.headers.get("X-Paste-Index"))
        total = int(request.headers.get("X-Paste-Total"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid chunk metadata."}), 400

    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        return jsonify({"error": "Invalid upload id."}), 400
    if total < 1 or total > _TRANSCRIPT_PASTE_CHUNK_MAX_PARTS or index < 0 or index >= total:
        return jsonify({"error": "Invalid chunk range."}), 400
    if len(chunk_b64) > 12000:
        return jsonify({"error": "Chunk header is too large."}), 431

    try:
        chunk = base64.b64decode(chunk_b64.encode("ascii"), validate=True).decode("utf-8")
        title = base64.b64decode(title_b64.encode("ascii"), validate=True).decode("utf-8") if title_b64 else ""
    except Exception:
        return jsonify({"error": "Invalid chunk encoding."}), 400

    if len(chunk) > _TRANSCRIPT_PASTE_CHUNK_MAX_CHARS:
        return jsonify({"error": "Chunk is too large."}), 413

    upload_dir = os.path.join(_TRANSCRIPT_PASTE_CHUNK_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    chunk_path = _paste_chunk_path(upload_id, index)
    with open(chunk_path, "w", encoding="utf-8") as fh:
        fh.write(chunk)

    _paste_logger.info(
        "header_chunk upload=%s index=%s/%s chars=%s remote=%s user=%s",
        upload_id[-8:],
        index + 1,
        total,
        len(chunk),
        request.headers.get("X-Forwarded-For") or request.remote_addr or "-",
        g.current_user["email"],
    )

    if index != total - 1:
        return jsonify({"received": index, "done": False})

    missing = [i for i in range(total) if not os.path.isfile(_paste_chunk_path(upload_id, i))]
    if missing:
        return jsonify({"error": "Some chunks are missing. Please submit again."}), 400

    try:
        parts = []
        for i in range(total):
            with open(_paste_chunk_path(upload_id, i), "r", encoding="utf-8") as fh:
                parts.append(fh.read())
        transcript = "".join(parts)
        _paste_logger.info(
            "header_upload_complete upload=%s chunks=%s chars=%s",
            upload_id[-8:],
            total,
            len(transcript),
        )
        job_id, error = _create_pasted_transcript_job(transcript, title)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"job_id": job_id, "cached": False, "done": True})
    finally:
        import shutil
        shutil.rmtree(upload_dir, ignore_errors=True)


@app.route("/transcript/temp-audio/<token>")
def transcript_temp_audio(token: str):
    """Serve a registered audio file to DashScope during transcription (no auth needed)."""
    import audio_registry
    path = audio_registry.lookup(token)
    if not path or not os.path.isfile(path):
        from flask import abort
        abort(404)
    return send_file(path)


@app.route("/transcript/status/<job_id>")
@login_required
def transcript_status(job_id: str):
    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    has_cached_audio = bool(job["audio_path"] and os.path.isfile(job["audio_path"]))
    include_content = request.args.get("include_content") != "0"
    return jsonify({
        "job_id":             job["job_id"],
        "status":             job["status"],
        "mode":               job["mode"],
        "video_title":        job["video_title"],
        "video_author":       job["video_author"],
        "video_url":          job["video_url"],
        "initiated_by":       job["initiated_by"],
        "input_type":         job["input_type"],
        "original_filename":  job["original_filename"],
        "summary":            job["summary"] if include_content else None,
        "transcript":         job["transcript"] if include_content else None,
        "transcript_zh":      job["transcript_zh"] if include_content else None,
        "content_omitted":    not include_content,
        "error_message":      job["error_message"],
        "has_cached_audio":   has_cached_audio,
    })


@app.route("/transcript/<job_id>/approve", methods=["POST"])
@login_required
def transcript_approve(job_id: str):
    from transcript_worker import continue_audio_transcript

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "awaiting_approval":
        return jsonify({"error": "Job is not awaiting approval."}), 400

    thread = threading.Thread(
        target=continue_audio_transcript,
        args=(job_id, job["video_id"]),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True})


@app.route("/transcript/<job_id>/summarize", methods=["POST"])
@login_required
def transcript_summarize(job_id: str):
    from transcript_worker import generate_transcript_summary

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "done":
        return jsonify({"error": "Transcript not ready."}), 400
    if not job["transcript"]:
        return jsonify({"error": "No transcript to summarize."}), 400

    db.update_transcript_job(job_id, status="summarizing")
    thread = threading.Thread(
        target=generate_transcript_summary,
        args=(job_id,),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True})


@app.route("/transcript/<job_id>/translate", methods=["POST"])
@login_required
def transcript_translate(job_id: str):
    from transcript_worker import translate_transcript

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "done":
        return jsonify({"error": "Transcript not ready."}), 400
    if not job["transcript"]:
        return jsonify({"error": "No transcript to translate."}), 400

    db.update_transcript_job(job_id, status="translating")
    threading.Thread(target=translate_transcript, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/transcript/<job_id>/retry", methods=["POST"])
@login_required
def transcript_retry(job_id: str):
    """Retry a failed transcription using the cached audio file (no re-download)."""
    from transcript_worker import retry_audio_transcript

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] not in ("error",):
        return jsonify({"error": "Job is not in error state."}), 400
    if not job["audio_path"] or not os.path.isfile(job["audio_path"]):
        return jsonify({"error": "No cached audio available — re-submit the URL to re-download."}), 400

    threading.Thread(target=retry_audio_transcript, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/transcript/<job_id>/delete", methods=["POST"])
@login_required
def transcript_delete(job_id: str):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    _delete_transcript_media_file(job)
    db.delete_transcript_job(job_id)
    return jsonify({"ok": True})


@app.route("/transcript/<job_id>/title", methods=["POST"])
@login_required
def transcript_update_title(job_id: str):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    if not db.get_transcript_job(job_id):
        return jsonify({"error": "Job not found."}), 404

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required."}), 400

    db.update_transcript_title(job_id, title)
    return jsonify({"ok": True, "title": title})


@app.route("/transcript/<job_id>/delete_summary", methods=["POST"])
@login_required
def transcript_delete_summary(job_id: str):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "Not authorised."}), 403
    if not db.get_transcript_job(job_id):
        return jsonify({"error": "Job not found."}), 404
    db.clear_transcript_summary(job_id)
    return jsonify({"ok": True})


def _safe_filename(title: str | None, fallback: str) -> str:
    """Sanitize a video title for use as a filename (cross-platform safe)."""
    import re
    if not title:
        return fallback
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title).strip()
    return safe[:80] or fallback


def _transcript_text_content(job, version: str) -> tuple[str, str, str]:
    """Return (text_content, label, suffix) for a transcript job."""
    video_url = job["video_url"]
    summary = job["summary"] or ""
    if version == "chinese":
        transcript = job["transcript_zh"] or ""
        label = "Chinese Version"
        suffix = "zh"
    else:
        transcript = job["transcript"] or ""
        label = "Original Version"
        suffix = "original"

    sections = [
        f"Transcript - {label}\n{'=' * 60}\n",
        f"Source URL: {video_url}\n\n" if video_url else "",
        f"Transcript\n{'-' * 60}\n{transcript}\n" if transcript else "",
    ]
    if summary:
        sections.insert(2, f"Summary\n{'-' * 60}\n{summary}\n\n{'=' * 60}\n")
    return "\n".join(s for s in sections if s), label, suffix


@app.route("/transcript/download/<job_id>")
@login_required
def transcript_download(job_id: str):
    from flask import Response

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "done":
        return jsonify({"error": "Transcript not ready yet."}), 400

    version = request.args.get("version", "original")
    if version == "chinese" and not job["transcript_zh"] and not job["summary"]:
        return jsonify({"error": "No Chinese content available."}), 400

    content, label, suffix = _transcript_text_content(job, version)
    base = _safe_filename(job["video_title"], job["video_id"])
    filename = f"{base}_{suffix}.txt"
    return Response(
        content,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{_url_quote(filename)}'},
    )


@app.route("/transcript/download/<job_id>/pdf")
@login_required
def transcript_download_pdf(job_id: str):
    from flask import Response as FlaskResponse
    from html import escape
    from playwright.sync_api import sync_playwright

    job = db.get_transcript_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "done":
        return jsonify({"error": "Transcript not ready yet."}), 400

    version = request.args.get("version", "original")
    if version == "chinese":
        transcript_text = job["transcript_zh"] or ""
        version_label = "中文版本"
    else:
        transcript_text = job["transcript"] or ""
        version_label = "原文版本"

    summary_text = job["summary"] or ""
    title = job["video_title"] or job["video_id"]
    author = job["video_author"] or ""
    video_url = job["video_url"]

    # Format transcript: highlight [Speaker X] labels
    import re
    def fmt_transcript(text: str) -> str:
        escaped = escape(text)
        return re.sub(
            r'\[(Speaker [A-Z])\]',
            r'<span class="speaker">[\1]</span>',
            escaped,
        )

    summary_html = ""
    if summary_text:
        # Convert **bold** markdown to <strong>
        formatted = escape(summary_text)
        formatted = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', formatted)
        formatted = formatted.replace('\n', '<br>')
        summary_html = f'<div class="summary">{formatted}</div>'

    transcript_html = f'<div class="transcript">{fmt_transcript(transcript_text)}</div>' if transcript_text else '<p class="none">（无转录文本）</p>'

    author_html = f'<br><span>作者：{escape(author)}</span>' if author else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 2.5cm; }}
  body {{
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "STSong", "SimSun",
                 "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 10.5pt; line-height: 1.8; color: #1a1a1a;
  }}
  h1 {{ font-size: 15pt; font-weight: bold; margin: 0 0 6pt 0; line-height: 1.4; }}
  .meta {{ font-size: 8.5pt; color: #555; margin-bottom: 18pt; }}
  .meta a {{ color: #555; text-decoration: none; }}
  h2 {{
    font-size: 11.5pt; font-weight: bold; margin: 20pt 0 8pt 0;
    padding-left: 8pt; border-left: 3pt solid #444; color: #222;
  }}
  .summary {{
    border-left: 3pt solid #888; padding: 8pt 12pt;
    margin-bottom: 20pt; font-size: 10pt; line-height: 1.9;
  }}
  .transcript {{
    white-space: pre-wrap; font-size: 10pt; line-height: 1.85;
    word-break: break-word;
  }}
  .speaker {{ font-weight: bold; color: #333; }}
  .none {{ color: #888; font-style: italic; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 16pt 0; }}
</style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <div class="meta">
    {author_html}
    <br>URL: <a href="{escape(video_url)}">{escape(video_url)}</a>
    <br>版本：{version_label}
  </div>
  {"<h2>中文摘要</h2>" + summary_html if summary_html else ""}
  {"<hr>" if summary_html else ""}
  <h2>完整转录文本</h2>
  {transcript_html}
</body>
</html>"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        pdf_bytes = page.pdf(
            format="A4",
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            print_background=False,
        )
        browser.close()

    pdf_bytes = _compress_pdf(pdf_bytes)

    base = _safe_filename(job["video_title"], job["video_id"])
    suffix = "zh" if version == "chinese" else "original"
    filename = f"{base}_{suffix}.pdf"
    return FlaskResponse(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{_url_quote(filename)}'},
    )


# ---------------------------------------------------------------------------
# External script report API
# ---------------------------------------------------------------------------

@app.route("/api/report", methods=["POST"])
def api_report_push():
    denied = _api_key_required()
    if denied:
        return denied
    data = request.get_json(force=True, silent=True) or {}
    script_name = (data.get("script") or "").strip()
    if not script_name:
        return jsonify({"error": "script name required"}), 400
    status = data.get("status", "ok")
    if status not in ("ok", "error"):
        status = "error"
    error_message = data.get("error") or None
    expected_interval_hours = float(data.get("expected_interval_hours", 24))
    panels = data.get("panels") or []
    import json as _json
    data_json = _json.dumps(panels) if panels else None
    db.upsert_script_report(script_name, status, error_message, data_json, expected_interval_hours)
    return jsonify({"ok": True})


@app.route("/api/report/<script_name>/reset", methods=["POST"])
def api_report_reset(script_name):
    """Delete a script's stored report + Excel file, for a clean-slate reset.

    API-key gated (same auth as the other /api/report routes) rather than
    session-gated, so it can be triggered non-interactively without a login.
    """
    denied = _api_key_required()
    if denied:
        return denied
    db.delete_script_data(script_name)
    return jsonify({"ok": True})


@app.route("/api/openrouter-usage/refresh", methods=["POST"])
def api_openrouter_usage_refresh():
    """Trigger an immediate OpenRouter usage fetch (normally runs on the
    weekly APScheduler job). API-key gated for non-interactive triggering."""
    denied = _api_key_required()
    if denied:
        return denied
    return jsonify(_run_openrouter_usage_fetch())


@app.route("/api/vercel-labs/refresh", methods=["POST"])
def api_vercel_labs_refresh():
    """Trigger an immediate Vercel Labs fetch. API-key gated for automation."""
    denied = _api_key_required()
    if denied:
        return denied
    return jsonify(_run_vercel_labs_fetch())


@app.route("/api/llm-token-expenditure-index/refresh", methods=["POST"])
def api_llm_token_expenditure_index_refresh():
    """Trigger an immediate Silicon Data token index fetch. API-key gated for automation."""
    denied = _api_key_required()
    if denied:
        return denied
    return jsonify(_run_llm_token_expenditure_index_fetch())


@app.route("/api/report/<script_name>/excel", methods=["POST"])
def api_report_excel_upload(script_name):
    denied = _api_key_required()
    if denied:
        return denied
    if "file" not in request.files:
        return jsonify({"error": "no file field"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400
    file_data = f.read()
    db.upsert_script_file(script_name, f.filename, file_data)
    return jsonify({"ok": True})


@app.route("/api/report/<script_name>/excel", methods=["GET"])
@login_required
def api_report_excel_download(script_name):
    row = db.get_script_file(script_name)
    if not row:
        return jsonify({"error": "not found"}), 404
    return send_file(
        io.BytesIO(row["file_data"]),
        download_name=row["filename"],
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/report/<script_name>/file/<file_key>", methods=["GET"])
@login_required
def api_report_file_download(script_name, file_key):
    row = db.get_script_file(script_name, file_key=file_key)
    if not row:
        return jsonify({"error": "not found"}), 404
    filename = row["filename"]
    mimetype = "text/csv; charset=utf-8" if filename.lower().endswith(".csv") else "application/octet-stream"
    return send_file(
        io.BytesIO(row["file_data"]),
        download_name=filename,
        as_attachment=True,
        mimetype=mimetype,
    )

@app.route("/dashboard/status")
@login_required
def dashboard_status():
    _ensure_llm_token_index_report()
    reports = db.get_all_script_reports()
    if not any(report["script_name"] == _GPU_STATUS_SCRIPT_NAME for report in reports):
        gpu_fallback_panel = _build_gpu_status_panel_fallback()
        if gpu_fallback_panel:
            reports.append(gpu_fallback_panel)
    return jsonify([
        {
            "script_name":  r["script_name"],
            "status":       r["status"],
            "pushed_at":    r["pushed_at"],
            "is_overdue":   r["is_overdue"],
        }
        for r in reports
    ])


# ---------------------------------------------------------------------------
# Dashboard — GPU prices (and future datasets)
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    _ensure_llm_token_index_report()
    is_admin = g.current_user["email"] == ADMIN_EMAIL
    all_panels = db.get_all_script_reports()
    if not any(panel["script_name"] == _GPU_STATUS_SCRIPT_NAME for panel in all_panels):
        gpu_fallback_panel = _build_gpu_status_panel_fallback()
        if gpu_fallback_panel:
            all_panels.append(gpu_fallback_panel)
    panel_access = db.get_panel_access()
    status_panels = []
    script_panels = []
    for panel in all_panels:
        panel = _clean_dashboard_report_panels(panel)
        is_gpu_status_panel = panel["script_name"] == _GPU_STATUS_SCRIPT_NAME
        access_key = "gpu-prices" if is_gpu_status_panel else panel["script_name"]
        if not is_admin and not panel_access.get(access_key, True):
            continue
        anchored_panel = _with_dashboard_anchor(panel)
        status_panels.append(anchored_panel)
        if not is_gpu_status_panel:
            script_panels.append(anchored_panel)
    scripts_with_files = db.get_scripts_with_files()
    return render_template(
        "dashboard.html",
        status_panels=status_panels,
        script_panels=script_panels,
        scripts_with_files=scripts_with_files,
        is_admin=is_admin,
        panel_access=panel_access,
    )


@app.route("/dashboard/panel-access/<panel_key>", methods=["POST"])
@login_required
def dashboard_toggle_panel_access(panel_key):
    """Admin-only: toggle a panel between public and admin-only."""
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    current = db.get_panel_access()
    new_public = not current.get(panel_key, True)
    db.set_panel_access(panel_key, new_public)
    return jsonify({"panel_key": panel_key, "public": new_public})


@app.route("/dashboard/api/gpu-prices")
@login_required
def dashboard_gpu_prices_api():
    """Return cached GPU price data as JSON.

    If the cache is empty (first visit), triggers a synchronous fetch so the
    page has data immediately. Subsequent loads hit the DB cache only.
    """
    cached = db.get_all_gpu_price_data()
    if not cached:
        # First-time: fetch synchronously (fast — 5 HTTP calls, ~2s total)
        threading.Thread(target=_run_gpu_price_fetch, daemon=True).start()
        return jsonify({"data": [], "fetching": True, "last_updated": None})

    last_updated = db.get_gpu_price_last_updated()
    return jsonify({
        "data": cached,
        "fetching": _gpu_fetch_running,
        "last_updated": last_updated,
    })


@app.route("/dashboard/gpu-prices/refresh", methods=["POST"])
@login_required
def dashboard_gpu_prices_refresh():
    """Trigger a background GPU price refresh."""
    if _gpu_fetch_running:
        return jsonify({"ok": False, "message": "已在刷新中"})
    threading.Thread(target=_run_gpu_price_fetch, daemon=True).start()
    return jsonify({"ok": True})



@app.route("/dashboard/popmart-youtube/refresh", methods=["POST"])
@login_required
def dashboard_popmart_youtube_refresh():
    """Admin-only: trigger a background POP MART YouTube refresh."""
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    if _popmart_youtube_fetch_running:
        return jsonify({"ok": False, "message": "already_running"})
    threading.Thread(target=_run_popmart_youtube_fetch, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
