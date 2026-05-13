"""Flask web UI for the news feed app."""

import logging
import os
import threading
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, redirect, render_template, request, url_for

import db
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
db.init_db()
db.seed_default_sources()

# Background fetch lock (prevent concurrent fetches from overlapping)
_fetch_lock = threading.Lock()
_fetch_status: dict = {"running": False, "last_result": None}

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


logger_sched = logging.getLogger("scheduler")
_scheduler = BackgroundScheduler(daemon=True)
_scheduler.add_job(_scheduled_nitter_fetch, "interval", hours=1, id="nitter_hourly")
_scheduler.start()


# ---------------------------------------------------------------------------
# News feed
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    date_from = request.args.get("date_from", week_ago)
    date_to = request.args.get("date_to", today)
    selected_source_ids = request.args.getlist("source_ids", type=int)

    all_sources = db.get_all_sources()
    source_ids = selected_source_ids if selected_source_ids else None

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
        date_from=date_from,
        date_to=date_to,
        fetch_status=_fetch_status,
    )


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------

@app.route("/sources", methods=["GET", "POST"])
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
    return render_template("sources.html", sources=all_sources, error=error)


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
def digest_generate():
    data = request.get_json(force=True, silent=True) or {}
    article_ids = data.get("article_ids", [])
    if not article_ids:
        return jsonify({"error": "No article IDs provided"}), 400
    digest = generate_batch_digest(article_ids)
    return jsonify({"digest": digest})


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
