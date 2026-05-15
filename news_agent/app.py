"""Flask web UI for the news feed app."""

import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

import db
from config import EMAIL_WHITELIST, NITTER_FETCH_INTERVAL_HOURS, SECRET_KEY
from digest import build_digest
from pipeline import run_fetch_and_summarize
from summarizer import generate_batch_digest, summarize_single_article

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
    """Fetch only Nitter sources — runs every hour while the app is active."""
    if _fetch_status["running"]:
        logger_sched.info("Skipping scheduled Nitter fetch — manual fetch in progress")
        return
    logger_sched.info("Scheduled Nitter fetch starting…")
    log_id = db.log_fetch_start(trigger="scheduled")
    try:
        result = run_fetch_and_summarize(source_types=["nitter"])
        db.log_fetch_finish(log_id, result)
        logger_sched.info("Scheduled Nitter fetch done: %d new article(s)", result["total_new"])
    except Exception as exc:
        db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
        logger_sched.error("Scheduled Nitter fetch error: %s", exc)


def _scheduled_digest_send():
    """
    For each user due for a digest:
      1. Fetch fresh articles from all their followed sources (RSS + web + nitter).
      2. Run the same AI digest pipeline used by the web UI (briefing, abstracts,
         big picture synthesis).
      3. Send the result by email.
    Runs every 6 hours; fetch is skipped if a manual fetch is already in progress.
    """
    from email_sender import send_digest as _send_email
    from summarizer import generate_batch_digest

    users = db.get_users_due_for_digest()
    if not users:
        return
    logger_sched.info("Digest check: %d user(s) due for email", len(users))

    for user in users:
        user = dict(user)
        days = user["digest_frequency_days"]
        date_from = (date.today() - timedelta(days=days)).isoformat()
        date_to = date.today().isoformat()
        followed = db.get_followed_source_ids(user["id"])
        source_ids = followed if followed else None

        try:
            # Step 1: fetch fresh articles for this user's sources
            if not _fetch_status["running"]:
                logger_sched.info("Digest pre-fetch for %s (sources: %s)", user["email"], source_ids)
                log_id = db.log_fetch_start(trigger="digest")
                try:
                    result = run_fetch_and_summarize(
                        summarize=False,
                        date_from=date_from,
                        date_to=date_to,
                        source_ids=source_ids,
                    )
                    db.log_fetch_finish(log_id, result)
                    logger_sched.info("Digest pre-fetch done: %d new article(s)", result["total_new"])
                except Exception as exc:
                    db.log_fetch_finish(log_id, {"total_new": 0, "sources": []}, error=str(exc))
                    logger_sched.warning("Digest pre-fetch failed: %s — continuing with existing articles", exc)
            else:
                logger_sched.info("Skipping digest pre-fetch for %s — manual fetch in progress", user["email"])

            # Step 2: get article IDs for the period
            articles = db.get_articles(date_from=date_from, date_to=date_to, source_ids=source_ids)
            article_ids = [a["id"] for a in articles]
            if not article_ids:
                logger_sched.info("No articles for %s in %s–%s, skipping email", user["email"], date_from, date_to)
                db.update_user_digest_last_sent(user["id"])
                continue

            # Step 3: generate AI digest (same pipeline as the web UI)
            logger_sched.info("Generating AI digest for %s (%d articles)", user["email"], len(article_ids))
            md = generate_batch_digest(article_ids)

            # Step 4: send
            ok = _send_email(md, to_email=user["email"], date_label=f"{date_from} to {date_to}")
            if ok:
                db.update_user_digest_last_sent(user["id"])
                logger_sched.info("Digest sent to %s", user["email"])

        except Exception as exc:
            logger_sched.error("Digest failed for %s: %s", user["email"], exc)


logger_sched = logging.getLogger("scheduler")
_scheduler = BackgroundScheduler(daemon=True)
_scheduler.add_job(_scheduled_nitter_fetch, "interval", hours=NITTER_FETCH_INTERVAL_HOURS, id="nitter_hourly")
_scheduler.add_job(_scheduled_digest_send, "interval", hours=6, id="digest_send")
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
    db.delete_source(source_id)
    return redirect(url_for("sources"))


@app.route("/scheduler/status")
def scheduler_status():
    job = _scheduler.get_job("nitter_hourly")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
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
            result = run_fetch_and_summarize(summarize=False, date_from=date_from, date_to=date_to, source_ids=source_ids)
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


# ---------------------------------------------------------------------------
# Article detail + on-demand summarization
# ---------------------------------------------------------------------------

@app.route("/articles/<int:article_id>")
def article_detail(article_id: int):
    row = db.get_article_by_id(article_id)
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


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

    def _run():
        try:
            result = generate_batch_digest(article_ids)
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
    md = build_digest(date_from=date_from, date_to=date_to)
    return app.response_class(md, mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Activity log viewer
# ---------------------------------------------------------------------------

LOG_FILE = "logs/app.log"
LOG_TAIL_LINES = 200


@app.route("/logs")
@login_required
def logs_view():
    fetch_log = [dict(r) for r in db.get_fetch_log(limit=100)]
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
        if freq not in (3, 7, 14):
            freq = 7
        db.update_user_digest_settings(g.current_user["id"], enabled, freq)
        g.current_user = db.get_user_by_id(g.current_user["id"])
        success = True
    return render_template("settings.html", user=g.current_user, success=success)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
