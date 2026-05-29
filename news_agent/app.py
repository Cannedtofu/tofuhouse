"""Flask web UI for the news feed app."""

import logging
import os
import threading
import uuid
from urllib.parse import quote as _url_quote
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for

import db
from config import ADMIN_EMAIL, EMAIL_WHITELIST, NITTER_FETCH_PERIOD_HOURS, SECRET_KEY
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

_file_handler = RotatingFileHandler(
    "logs/app.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB per file
    backupCount=5,
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


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@app.before_request
def load_current_user():
    uid = session.get("user_id")
    g.current_user = db.get_user_by_id(uid) if uid else None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("identify", next=request.path))
        return f(*args, **kwargs)
    return decorated

# Background fetch lock (prevent concurrent fetches from overlapping)
_fetch_lock = threading.Lock()
_fetch_status: dict = {"running": False, "last_result": None}

# Digest jobs — keyed by UUID, each: {"status": "running"|"done"|"error", "result": str}
_digest_jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Periodic background scheduler
# ---------------------------------------------------------------------------

def _scheduled_nitter_fetch():
    """Fetch only Nitter sources — runs at fixed clock times anchored to 11pm SGT."""
    if _fetch_status["running"]:
        logger_sched.info("Skipping scheduled Nitter fetch — manual fetch in progress")
        return
    _fetch_status["running"] = True
    logger_sched.info("Scheduled Nitter fetch starting…")
    log_id = db.log_fetch_start(trigger="scheduled")
    try:
        result = run_fetch_and_summarize(source_types=["nitter"])
        db.log_fetch_finish(log_id, result)
        logger_sched.info("Scheduled Nitter fetch done: %d new article(s)", result["total_new"])
    except Exception as exc:
        db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
        logger_sched.error("Scheduled Nitter fetch error: %s", exc)
    finally:
        _fetch_status["running"] = False


def _scheduled_digest_send():
    """
    Send email digests to all users due for one.
    All required sources are fetched once (union of followed sources, widest date
    window) before the per-user digest/email loop, eliminating redundant fetches
    when multiple users share sources.
    Runs every 6 hours; fetch is skipped if a manual fetch is already in progress.
    """
    from email_sender import send_digest as _send_email
    from ai_digest import generate_batch_digest

    users = db.get_users_due_for_digest()
    if not users:
        return

    users = [dict(u) for u in users]
    logger_sched.info("Digest check: %d user(s) due for email", len(users))

    date_to = date.today().isoformat()

    # Resolve each user's date window and followed sources upfront.
    for user in users:
        user["_date_from"] = (date.today() - timedelta(days=user["digest_frequency_days"])).isoformat()
        followed = db.get_followed_source_ids(user["id"])
        user["_source_ids"] = followed if followed else None

    # --- Single unified fetch ---
    # Use the union of all users' followed sources (None = all sources).
    if any(u["_source_ids"] is None for u in users):
        all_source_ids = None
    else:
        all_source_ids = list({sid for u in users for sid in u["_source_ids"]})

    # Use the oldest date_from so the fetch covers every user's lookback window.
    min_date_from = min(u["_date_from"] for u in users)

    if not _fetch_status["running"]:
        logger_sched.info(
            "Digest pre-fetch: %s source(s), from %s",
            len(all_source_ids) if all_source_ids is not None else "all",
            min_date_from,
        )
        log_id = db.log_fetch_start(trigger="digest")
        try:
            result = run_fetch_and_summarize(
                summarize=False,
                date_from=min_date_from,
                date_to=date_to,
                source_ids=all_source_ids,
            )
            db.log_fetch_finish(log_id, result)
            logger_sched.info("Digest pre-fetch done: %d new article(s)", result["total_new"])
        except Exception as exc:
            db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
            logger_sched.warning("Digest pre-fetch failed: %s — continuing with existing articles", exc)
    else:
        logger_sched.info("Skipping digest pre-fetch — manual fetch in progress")

    # --- Per-user digest generation and email ---
    for user in users:
        date_from = user["_date_from"]
        source_ids = user["_source_ids"]

        try:
            articles = db.get_articles(date_from=date_from, date_to=date_to, source_ids=source_ids)
            article_ids = [a["id"] for a in articles]
            if not article_ids:
                logger_sched.info("No articles for %s in %s–%s, skipping email", user["email"], date_from, date_to)
                db.update_user_digest_last_sent(user["id"])
                continue

            logger_sched.info("Generating AI digest for %s (%d articles)", user["email"], len(article_ids))
            md = generate_batch_digest(article_ids, user_id=user["id"])

            ok = _send_email(md, to_email=user["email"], date_label=f"{date_from} to {date_to}")
            if ok:
                db.update_user_digest_last_sent(user["id"])
                logger_sched.info("Digest sent to %s", user["email"])

        except Exception as exc:
            logger_sched.error("Digest failed for %s: %s", user["email"], exc)

logger_sched = logging.getLogger("scheduler")
_scheduler = BackgroundScheduler(daemon=True)

# Anchor Nitter fetches to 11pm SGT (15:00 UTC). If NITTER_FETCH_PERIOD_HOURS
# is less than 24 and divides evenly into 24, add runs at equal intervals from
# that anchor (e.g. 12h → 15:00 + 03:00 UTC; 8h → 07:00 + 15:00 + 23:00 UTC).
_NITTER_ANCHOR_UTC = 15  # 11pm SGT = UTC+8
if 24 % NITTER_FETCH_PERIOD_HOURS == 0:
    _nitter_cron_hours = ",".join(
        str((_NITTER_ANCHOR_UTC + i * NITTER_FETCH_PERIOD_HOURS) % 24)
        for i in range(24 // NITTER_FETCH_PERIOD_HOURS)
    )
else:
    # Non-divisible interval: fall back to single daily run at anchor time
    _nitter_cron_hours = str(_NITTER_ANCHOR_UTC)

_scheduler.add_job(_scheduled_nitter_fetch, "cron", hour=_nitter_cron_hours, minute=0, id="nitter_periodic")
# Fixed SGT times (server runs UTC+8): 03:00, 09:00, 15:00, 21:00 — no drift on restart
_scheduler.add_job(_scheduled_digest_send, "cron", hour="3,9,15,21", minute=0, id="digest_send")


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

    return render_template(
        "index.html",
        articles=articles_list,
        grouped_articles=grouped_articles,
        all_sources=[dict(s) for s in all_sources],
        selected_source_ids=selected_source_ids,
        followed_source_ids=followed_ids,
        date_from=date_from,
        date_to=date_to,
        fetch_status=_fetch_status,
        is_admin=g.current_user["email"] == ADMIN_EMAIL,
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
        elif type_ not in ("rss", "nitter", "web"):
            error = "Type must be rss, nitter, or web."
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


@app.route("/sources/<int:source_id>/follow", methods=["POST"])
@login_required
def toggle_follow(source_id: int):
    action = request.form.get("action", "follow")
    if action == "unfollow":
        db.unfollow_source(g.current_user["id"], source_id)
    else:
        db.follow_source(g.current_user["id"], source_id)
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
    job = _scheduler.get_job("nitter_periodic")
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


# ---------------------------------------------------------------------------
# Batch digest (all visible articles → one AI summary)
# ---------------------------------------------------------------------------

@app.route("/digest/generate", methods=["POST"])
@login_required
def digest_generate():
    data = request.get_json(force=True, silent=True) or {}
    article_ids = data.get("article_ids", [])
    if not article_ids:
        return jsonify({"error": "No article IDs provided"}), 400

    job_id = str(uuid.uuid4())
    _digest_jobs[job_id] = {"status": "running", "result": None}

    uid = g.current_user["id"] if g.current_user else None

    def _run():
        try:
            result = generate_batch_digest(article_ids, user_id=uid)
            _digest_jobs[job_id] = {"status": "done", "result": result}
        except Exception as exc:
            logging.exception("Digest generation failed")
            _digest_jobs[job_id] = {"status": "error", "result": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


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

LOG_FILE = "logs/app.log"
LOG_TAIL_LINES = 200


@app.route("/logs/download")
@login_required
def logs_download():
    if not os.path.exists(LOG_FILE):
        return "Log file not found.", 404
    return send_file(
        os.path.abspath(LOG_FILE),
        mimetype="text/plain",
        as_attachment=True,
        download_name="app.log",
    )


@app.route("/logs")
@login_required
def logs_view():
    fetch_log = [dict(r) for r in db.get_fetch_log(limit=20)]
    # Parse sources_json for display
    import json as _json
    for entry in fetch_log:
        try:
            entry["sources_parsed"] = _json.loads(entry["sources_json"] or "[]")
        except Exception:
            entry["sources_parsed"] = []

    # Read last N lines of the log file
    raw_lines: list[str] = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
        raw_lines = raw_lines[-LOG_TAIL_LINES:]
    except FileNotFoundError:
        pass

    return render_template("logs.html", fetch_log=fetch_log, raw_lines=raw_lines)


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

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
    if is_admin:
        for u in all_users:
            user_follows[u["id"]] = db.get_all_sources_with_follow_status(u["id"])
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
    )


@app.route("/admin/users/<int:user_id>/follows", methods=["POST"])
@login_required
def admin_update_user_follows(user_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    checked_ids = [int(v) for v in request.form.getlist("source_ids")]
    db.set_user_follows(user_id, checked_ids)
    return redirect(url_for("settings") + f"#follows-{user_id}")


@app.route("/admin/users/<int:user_id>/digest", methods=["POST"])
@login_required
def admin_update_user_digest(user_id: int):
    if g.current_user["email"] != ADMIN_EMAIL:
        return jsonify({"error": "forbidden"}), 403
    enabled = request.form.get("digest_enabled") == "1"
    try:
        freq = int(request.form.get("digest_frequency_days", 7))
    except ValueError:
        freq = 7
    if freq not in (1, 3, 7, 14):
        freq = 7
    db.update_user_digest_settings(user_id, enabled, freq)
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
            "created_at":   j["created_at"],
        }
        for j in jobs
    ])


@app.route("/transcript/process", methods=["POST"])
@login_required
def transcript_process():
    from transcript_worker import extract_video_id, is_youtube_url, process_transcript_job

    data   = request.get_json(silent=True) or {}
    url    = (data.get("url")  or "").strip()
    mode   = (data.get("mode") or "no_diarization").strip()

    if mode not in ("no_diarization", "diarization"):
        return jsonify({"error": "Invalid mode."}), 400
    if not url:
        return jsonify({"error": "URL is required."}), 400
    if not is_youtube_url(url):
        return jsonify({"error": "Not a recognized YouTube video URL."}), 400

    video_id = extract_video_id(url)

    # Return cached result if a completed job already exists for this video+mode
    cached = db.get_done_transcript_job(video_id, mode)
    if cached:
        return jsonify({"job_id": cached["job_id"], "cached": True})

    job_id = db.create_transcript_job(video_url=url, video_id=video_id, mode=mode)

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
    background: #f7f7f7; padding: 12pt 14pt; border-radius: 4pt;
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
            print_background=True,
        )
        browser.close()

    base = _safe_filename(job["video_title"], job["video_id"])
    suffix = "zh" if version == "chinese" else "original"
    filename = f"{base}_{suffix}.pdf"
    return FlaskResponse(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{_url_quote(filename)}'},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
