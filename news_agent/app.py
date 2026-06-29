"""Flask web UI for the news feed app."""

import io
import logging
import os
import threading
import uuid
from urllib.parse import quote as _url_quote
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from logging.handlers import TimedRotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for

import db
from config import ADMIN_EMAIL, EMAIL_WHITELIST, REPORT_API_KEY, SECRET_KEY
from email_digest import build_email_digest
from pipeline import run_fetch_and_summarize
from ai_digest import generate_batch_digest
from article_summarizer import summarize_single_article

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

app = Flask(__name__)
app.secret_key = SECRET_KEY

_SGT = timezone(timedelta(hours=8))

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
def load_current_user():
    uid = session.get("user_id")
    g.current_user = db.get_user_by_id(uid) if uid else None


@app.context_processor
def inject_template_globals():
    return {
        "is_admin": bool(g.current_user and g.current_user["email"] == ADMIN_EMAIL)
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
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

# Digest jobs — keyed by UUID, each: {"status": "running"|"done"|"error", "result": str}
_digest_jobs: dict[str, dict] = {}

# Article translation jobs — keyed by UUID
_article_translation_jobs: dict[str, dict] = {}

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

logger_sched = logging.getLogger("scheduler")
logger_dashboard = logging.getLogger("dashboard")

_gpu_fetch_lock = threading.Lock()
_gpu_fetch_running = False


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
        logger_dashboard.info("GPU price fetch done: %d GPU type(s) updated", len(results))
    except Exception as exc:
        logger_dashboard.error("GPU price fetch error: %s", exc)
    finally:
        _gpu_fetch_running = False
        _gpu_fetch_lock.release()


def _run_openrouter_usage_fetch():
    """Fetch weekly OpenRouter token-usage data and push panels + Excel to the dashboard.

    Runs in-process (no HTTP round-trip to our own /api/report — this server
    IS the dashboard), writing directly to the same script_reports/script_files
    tables that the external-script API route writes to.
    """
    import json as _json
    try:
        from fetchers.openrouter_usage import run_openrouter_usage_fetch
        logger_dashboard.info("OpenRouter usage fetch starting…")
        panels, excel_bytes = run_openrouter_usage_fetch()
        db.upsert_script_report(
            "openrouter_usage", "ok", None, _json.dumps(panels), 168,  # 168h = 7 days
        )
        db.upsert_script_file("openrouter_usage", "openrouter_usage.xlsx", excel_bytes)
        logger_dashboard.info("OpenRouter usage fetch done: %d panels", len(panels))
    except Exception as exc:
        logger_dashboard.error("OpenRouter usage fetch error: %s", exc)
        db.upsert_script_report("openrouter_usage", "error", str(exc), None, 168)


# Explicit SGT timezone so all cron hours are unambiguous regardless of server clock.
_scheduler = BackgroundScheduler(daemon=True, timezone="Asia/Singapore")

_scheduler.add_job(_scheduled_daily_fetch, "cron", hour=5,  minute=0, id="daily_fetch")   # 05:00 SGT
_scheduler.add_job(_scheduled_digest_send, "cron", hour=9,  minute=0, id="digest_send")   # 09:00 SGT
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

_scheduler.start()


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

@app.route("/")
@login_required
def index():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    date_from = request.args.get("date_from", week_ago)
    date_to = request.args.get("date_to", today)
    selected_source_ids = request.args.getlist("source_ids", type=int)

    all_sources = db.get_all_sources()
    followed_ids = db.get_followed_source_ids(g.current_user["id"])

    # Use explicitly selected sources (from filter form), else the user's followed list.
    # followed_ids is always populated on sign-in, so this is always user-specific.
    if selected_source_ids:
        source_ids = selected_source_ids
    else:
        source_ids = followed_ids if followed_ids else None

    articles = db.get_articles(
        date_from=date_from,
        date_to=date_to,
        source_ids=source_ids,
    )
    articles_list = [dict(a) for a in articles]

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
        selected_source_ids=selected_source_ids,
        followed_source_ids=followed_ids,
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
    source_ids = data.get("source_ids") or None  # None = all sources

    def _run():
        _fetch_status["running"] = True
        log_id = db.log_fetch_start(trigger="manual")
        try:
            result = run_fetch_and_summarize(
                summarize=False,
                date_from=date_from,
                date_to=date_to,
                source_ids=source_ids,
            )
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
    all_sources = [dict(s) for s in db.get_all_sources()]
    return render_template("subscribe.html", digest_presets=digest_presets, all_sources=all_sources)


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
    browser_summary = db.get_token_usage_summary()  # global — includes browser_agent
    browser_rows = [r for r in browser_summary if r["operation"] == "browser_agent"]
    is_admin = g.current_user["email"] == ADMIN_EMAIL
    all_users = db.get_all_users() if is_admin else []
    weekly_tokens = db.get_token_usage_by_user_week() if is_admin else []
    # For the follow-list editor: sources annotated per user
    user_follows = {}
    # For the preset digest editor: flat list of {email, preset_id, preset_name, ...}
    admin_digest_rows = []
    if is_admin:
        for u in all_users:
            user_follows[u["id"]] = db.get_all_sources_with_follow_status(u["id"])
        all_preset_list = db.get_digest_presets_for_users([u["id"] for u in all_users])
        presets_by_user = {}
        for p in all_preset_list:
            presets_by_user.setdefault(p["user_id"], []).append(p)
        for u in all_users:
            user_presets = presets_by_user.get(u["id"], [])
            if not user_presets:
                admin_digest_rows.append({
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
            "created_at":   j["created_at"],
        }
        for j in jobs
    ])


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
    return jsonify({
        "job_id":             job["job_id"],
        "status":             job["status"],
        "mode":               job["mode"],
        "video_title":        job["video_title"],
        "video_author":       job["video_author"],
        "video_url":          job["video_url"],
        "initiated_by":       job["initiated_by"],
        "summary":            job["summary"],
        "transcript":         job["transcript"],
        "transcript_zh":      job["transcript_zh"],
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
    db.delete_transcript_job(job_id)
    return jsonify({"ok": True})


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
        label = "中文版本"
        suffix = "zh"
    else:
        transcript = job["transcript"] or ""
        label = "原文版本"
        suffix = "original"

    sections = [
        f"YouTube Transcript — {label}\nURL: {video_url}\n{'=' * 60}\n",
        f"完整转录文本\n{'-' * 60}\n{transcript}\n" if transcript else "",
    ]
    if summary:
        sections.insert(1, f"中文摘要\n{'-' * 60}\n{summary}\n\n{'=' * 60}\n")
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
    _run_openrouter_usage_fetch()
    return jsonify({"ok": True})


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


@app.route("/dashboard/status")
@login_required
def dashboard_status():
    reports = db.get_all_script_reports()
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
    is_admin = g.current_user["email"] == ADMIN_EMAIL
    all_panels = db.get_all_script_reports()
    panel_access = db.get_panel_access()
    # Non-admins only see panels where public=True (default when not in table)
    if is_admin:
        script_panels = all_panels
    else:
        script_panels = [p for p in all_panels if panel_access.get(p["script_name"], True)]
    scripts_with_files = db.get_scripts_with_files()
    return render_template(
        "dashboard.html",
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
