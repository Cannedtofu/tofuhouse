"""SQLite database layer for news_agent."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH


@contextmanager
def get_conn():
    # check_same_thread=False: each call creates its own connection used within
    # one thread, so there is no cross-thread sharing. timeout=30 lets background
    # workers wait up to 30 s for WAL write locks instead of raising immediately.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                type         TEXT    NOT NULL CHECK(type IN ('rss','nitter','web')),
                url          TEXT    NOT NULL UNIQUE,
                url_filter   TEXT,
                last_fetched TEXT
            );

            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                title        TEXT,
                url          TEXT    NOT NULL UNIQUE,
                content      TEXT,
                published_at TEXT,
                fetched_at   TEXT    NOT NULL,
                summary      TEXT
            );

            CREATE TABLE IF NOT EXISTS fetch_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at   TEXT    NOT NULL,
                finished_at  TEXT,
                trigger      TEXT    NOT NULL DEFAULT 'manual',
                total_new    INTEGER NOT NULL DEFAULT 0,
                total_fetched INTEGER NOT NULL DEFAULT 0,
                sources_json TEXT,
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT    NOT NULL,
                last_seen  TEXT
            );

            CREATE TABLE IF NOT EXISTS user_source_follows (
                user_id   INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, source_id)
            );

            CREATE TABLE IF NOT EXISTS digests (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                article_ids_hash TEXT    NOT NULL UNIQUE,
                article_ids_json TEXT    NOT NULL,
                content          TEXT    NOT NULL,
                created_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS token_usage (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
                operation  TEXT    NOT NULL,
                model      TEXT    NOT NULL,
                tokens_in  INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_articles_source   ON articles(source_id);
            CREATE INDEX IF NOT EXISTS idx_articles_pub_date ON articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_fetch_log_started ON fetch_log(started_at);
            CREATE INDEX IF NOT EXISTS idx_usf_user          ON user_source_follows(user_id);
            CREATE INDEX IF NOT EXISTS idx_token_usage_user  ON token_usage(user_id);

            CREATE TABLE IF NOT EXISTS transcript_jobs (
                job_id        TEXT    PRIMARY KEY,
                video_url     TEXT    NOT NULL,
                video_id      TEXT    NOT NULL,
                video_title   TEXT,
                video_author  TEXT,
                mode          TEXT    NOT NULL DEFAULT 'no_diarization',
                status        TEXT    NOT NULL DEFAULT 'pending',
                transcript    TEXT,
                summary       TEXT,
                error_message TEXT,
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_transcript_jobs_created ON transcript_jobs(created_at);
        """)
        # Migrations for older databases
        try:
            conn.execute("ALTER TABLE sources ADD COLUMN url_filter TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE fetch_log ADD COLUMN total_fetched INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN digest_abstract TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN digest_enabled INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN digest_frequency_days INTEGER NOT NULL DEFAULT 7")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN digest_last_sent TEXT")
        except Exception:
            pass
        # Recreate transcript_jobs to: add mode column, drop restrictive CHECK constraint
        _cols = [r[1] for r in conn.execute("PRAGMA table_info(transcript_jobs)").fetchall()]
        if "mode" not in _cols:
            conn.executescript("""
                PRAGMA foreign_keys=OFF;
                CREATE TABLE transcript_jobs_new (
                    job_id        TEXT    PRIMARY KEY,
                    video_url     TEXT    NOT NULL,
                    video_id      TEXT    NOT NULL,
                    mode          TEXT    NOT NULL DEFAULT 'no_diarization',
                    status        TEXT    NOT NULL DEFAULT 'pending',
                    transcript    TEXT,
                    summary       TEXT,
                    error_message TEXT,
                    created_at    TEXT    NOT NULL,
                    updated_at    TEXT    NOT NULL
                );
                INSERT INTO transcript_jobs_new
                    (job_id, video_url, video_id, status, transcript, summary,
                     error_message, created_at, updated_at)
                    SELECT job_id, video_url, video_id, status, transcript, summary,
                           error_message, created_at, updated_at
                    FROM transcript_jobs;
                DROP TABLE transcript_jobs;
                ALTER TABLE transcript_jobs_new RENAME TO transcript_jobs;
                CREATE INDEX IF NOT EXISTS idx_transcript_jobs_created ON transcript_jobs(created_at);
                CREATE INDEX IF NOT EXISTS idx_transcript_jobs_video   ON transcript_jobs(video_id, mode);
                PRAGMA foreign_keys=ON;
            """)
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_jobs_video"
                " ON transcript_jobs(video_id, mode)"
            )
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE transcript_jobs ADD COLUMN video_title TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE transcript_jobs ADD COLUMN video_author TEXT")
        except Exception:
            pass


def seed_default_sources():
    """Insert DEFAULT_SOURCES into the DB if no sources exist yet."""
    from config import DEFAULT_SOURCES
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        if count > 0:
            return
    for s in DEFAULT_SOURCES:
        upsert_source(
            name=s["name"],
            type_=s["type"],
            url=s["url"],
            url_filter=s.get("url_filter"),
        )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def get_all_sources() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sources ORDER BY name"
        ).fetchall()


def get_source_by_id(source_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()


def upsert_source(name: str, type_: str, url: str, url_filter: Optional[str] = None) -> int:
    """Insert or update a source; returns its id."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sources (name, type, url, url_filter)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   name=excluded.name,
                   type=excluded.type,
                   url_filter=excluded.url_filter""",
            (name, type_, url, url_filter),
        )
        row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
        return row["id"]


def delete_source(source_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))


def delete_article(article_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


def update_source_last_fetched(source_id: int):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_fetched = ? WHERE id = ?", (now, source_id)
        )


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def insert_article(
    source_id: int,
    title: str,
    url: str,
    content: str,
    published_at: Optional[str],
) -> bool:
    """Insert article; returns True if newly inserted, False if already existed.
    If the article already exists and the new content is longer, the content is updated.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, content FROM articles WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            # Update content if the new version is richer than what's stored
            if content and len(content) > len(existing["content"] or ""):
                conn.execute(
                    "UPDATE articles SET content = ? WHERE id = ?",
                    (content, existing["id"]),
                )
            return False
        conn.execute(
            """INSERT INTO articles
               (source_id, title, url, content, published_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, title, url, content, published_at, fetched_at),
        )
        return True


def get_article_by_id(article_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT a.*, s.name AS source_name, s.type AS source_type
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.id = ?""",
            (article_id,),
        ).fetchone()


def get_unsummarized_articles() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM articles WHERE summary IS NULL ORDER BY fetched_at"
        ).fetchall()


def update_summary(article_id: int, summary: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET summary = ? WHERE id = ?", (summary, article_id)
        )


def get_articles(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_ids: Optional[list[int]] = None,
) -> list[sqlite3.Row]:
    """
    Query articles with optional filters.
    date_from / date_to: ISO date strings like '2026-04-10'
    source_ids: list of source IDs to include; None means all
    """
    query = """
        SELECT a.*, s.name AS source_name, s.type AS source_type
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE 1=1
    """
    params: list = []

    if date_from:
        query += " AND (a.published_at >= ? OR (a.published_at IS NULL AND a.fetched_at >= ?))"
        params += [date_from, date_from]
    if date_to:
        # include the full end day
        date_to_end = date_to + "T23:59:59"
        query += " AND (a.published_at <= ? OR (a.published_at IS NULL AND a.fetched_at <= ?))"
        params += [date_to_end, date_to_end]
    if source_ids:
        placeholders = ",".join("?" * len(source_ids))
        query += f" AND a.source_id IN ({placeholders})"
        params += source_ids

    query += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC"

    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


# ---------------------------------------------------------------------------
# Fetch log
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_or_create_user(email: str) -> sqlite3.Row:
    """Return the user row for email, creating it if it doesn't exist.
    Updates last_seen on every call. Email is normalised to lowercase.
    """
    email = email.strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, existing["id"]))
            return conn.execute("SELECT * FROM users WHERE id = ?", (existing["id"],)).fetchone()
        conn.execute(
            "INSERT INTO users (email, created_at, last_seen) VALUES (?, ?, ?)",
            (email, now, now),
        )
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ---------------------------------------------------------------------------
# User source follows
# ---------------------------------------------------------------------------

def get_followed_source_ids(user_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source_id FROM user_source_follows WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [r["source_id"] for r in rows]


def follow_source(user_id: int, source_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_source_follows (user_id, source_id) VALUES (?, ?)",
            (user_id, source_id),
        )


def unfollow_source(user_id: int, source_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_source_follows WHERE user_id = ? AND source_id = ?",
            (user_id, source_id),
        )


def get_all_sources_with_follow_status(user_id: int) -> list[sqlite3.Row]:
    """Return all sources with a `followed` boolean for the given user."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT s.id, s.name, s.type,
                      CASE WHEN f.source_id IS NOT NULL THEN 1 ELSE 0 END AS followed
               FROM sources s
               LEFT JOIN user_source_follows f
                 ON f.source_id = s.id AND f.user_id = ?
               ORDER BY s.name""",
            (user_id,),
        ).fetchall()


def set_user_follows(user_id: int, source_ids: list[int]):
    """Replace a user's entire follow list with the given source IDs."""
    with get_conn() as conn:
        conn.execute("DELETE FROM user_source_follows WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO user_source_follows (user_id, source_id) VALUES (?, ?)",
            [(user_id, sid) for sid in source_ids],
        )


# ---------------------------------------------------------------------------
# User digest settings
# ---------------------------------------------------------------------------

def update_user_digest_settings(user_id: int, enabled: bool, frequency_days: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET digest_enabled=?, digest_frequency_days=? WHERE id=?",
            (1 if enabled else 0, frequency_days, user_id),
        )


def update_user_digest_last_sent(user_id: int):
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET digest_last_sent=? WHERE id=?",
            (today, user_id),
        )


# ---------------------------------------------------------------------------
# Digest abstract cache (per-article)
# ---------------------------------------------------------------------------

def get_digest_abstract(article_id: int) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT digest_abstract FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return row["digest_abstract"] if row else None


def update_digest_abstract(article_id: int, abstract: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET digest_abstract = ? WHERE id = ?", (abstract, article_id)
        )


# ---------------------------------------------------------------------------
# Full digest cache
# ---------------------------------------------------------------------------

def get_all_digests_with_meta(limit: int = 100) -> list[dict]:
    """Return all digests with date range and source names derived from their article IDs."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, article_ids_json, content, created_at FROM digests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for row in rows:
        article_ids = json.loads(row["article_ids_json"] or "[]")
        date_from = date_to = None
        sources: list[str] = []

        if article_ids:
            placeholders = ",".join("?" * len(article_ids))
            with get_conn() as conn:
                meta = conn.execute(
                    f"""SELECT MIN(COALESCE(a.published_at, a.fetched_at)) AS date_from,
                               MAX(COALESCE(a.published_at, a.fetched_at)) AS date_to,
                               GROUP_CONCAT(DISTINCT s.name) AS sources
                        FROM articles a
                        JOIN sources s ON s.id = a.source_id
                        WHERE a.id IN ({placeholders})""",
                    article_ids,
                ).fetchone()
            if meta:
                date_from = (meta["date_from"] or "")[:10] or None
                date_to   = (meta["date_to"]   or "")[:10] or None
                sources   = sorted(set(meta["sources"].split(",") if meta["sources"] else []))

        result.append({
            "id":            row["id"],
            "created_at":    row["created_at"],
            "content":       row["content"],
            "article_count": len(article_ids),
            "date_from":     date_from,
            "date_to":       date_to,
            "sources":       sources,
        })

    return result


def get_digest_cache(article_ids_hash: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM digests WHERE article_ids_hash = ?", (article_ids_hash,)
        ).fetchone()
        return row["content"] if row else None


def save_digest_cache(article_ids_hash: str, article_ids_json: str, content: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO digests (article_ids_hash, article_ids_json, content, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(article_ids_hash) DO UPDATE SET content=excluded.content, created_at=excluded.created_at""",
            (article_ids_hash, article_ids_json, content, now),
        )


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def log_token_usage(
    operation: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    user_id: Optional[int] = None,
):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage (user_id, operation, model, tokens_in, tokens_out, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, operation, model, tokens_in, tokens_out, now),
        )


def get_token_usage_summary(user_id: Optional[int] = None) -> list[sqlite3.Row]:
    """Totals grouped by operation + model. Pass user_id=None for global (browser_agent)."""
    with get_conn() as conn:
        if user_id is not None:
            return conn.execute(
                """SELECT operation, model,
                          SUM(tokens_in) AS total_in, SUM(tokens_out) AS total_out,
                          COUNT(*) AS calls
                   FROM token_usage WHERE user_id = ?
                   GROUP BY operation, model ORDER BY operation""",
                (user_id,),
            ).fetchall()
        return conn.execute(
            """SELECT operation, model,
                      SUM(tokens_in) AS total_in, SUM(tokens_out) AS total_out,
                      COUNT(*) AS calls
               FROM token_usage
               GROUP BY operation, model ORDER BY operation""",
        ).fetchall()


def get_all_users() -> list[sqlite3.Row]:
    """Return all users with their digest settings, ordered by email."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT id, email, digest_enabled, digest_frequency_days, digest_last_sent, last_seen
               FROM users ORDER BY email"""
        ).fetchall()


def get_token_usage_by_user_week() -> list[sqlite3.Row]:
    """Token usage per user for the past 7 days, grouped by user email."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT COALESCE(u.email, '(system)') AS email,
                      SUM(t.tokens_in)  AS total_in,
                      SUM(t.tokens_out) AS total_out,
                      COUNT(*)          AS calls
               FROM token_usage t
               LEFT JOIN users u ON u.id = t.user_id
               WHERE t.created_at >= date('now', '-7 days')
               GROUP BY t.user_id
               ORDER BY (total_in + total_out) DESC"""
        ).fetchall()


def get_users_due_for_digest() -> list[sqlite3.Row]:
    """Return users who have digest enabled and are due for their next send."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM users
            WHERE digest_enabled = 1
            AND (
                digest_last_sent IS NULL
                OR date(digest_last_sent, '+' || digest_frequency_days || ' days') <= date('now')
            )
        """).fetchall()


# ---------------------------------------------------------------------------
# YouTube transcript jobs
# ---------------------------------------------------------------------------

def create_transcript_job(video_url: str, video_id: str, mode: str = "no_diarization") -> str:
    """Insert a new transcript job with status 'pending'. Returns the job_id (UUID)."""
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transcript_jobs
               (job_id, video_url, video_id, mode, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (job_id, video_url, video_id, mode, now, now),
        )
    return job_id


def get_done_transcript_job(video_id: str, mode: str) -> Optional[sqlite3.Row]:
    """Return the most recent completed job for this video+mode, or None."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM transcript_jobs
               WHERE video_id = ? AND mode = ? AND status = 'done'
               ORDER BY created_at DESC LIMIT 1""",
            (video_id, mode),
        ).fetchone()


def list_transcript_jobs(limit: int = 60) -> list:
    """Return recent transcript jobs ordered newest-first (for sidebar)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT job_id, video_id, video_url, video_title, video_author, mode, status, created_at
               FROM transcript_jobs
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def update_transcript_job(
    job_id: str,
    status: str,
    transcript: Optional[str] = None,
    summary: Optional[str] = None,
    error_message: Optional[str] = None,
):
    """Update a transcript job's status and optional result fields."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE transcript_jobs
               SET status=?, transcript=COALESCE(?, transcript),
                   summary=COALESCE(?, summary),
                   error_message=COALESCE(?, error_message),
                   updated_at=?
               WHERE job_id=?""",
            (status, transcript, summary, error_message, now, job_id),
        )


def get_transcript_job(job_id: str) -> Optional[sqlite3.Row]:
    """Return the transcript job row for the given job_id, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transcript_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()


def set_transcript_metadata(job_id: str, video_title: Optional[str], video_author: Optional[str]) -> None:
    """Store video title and author fetched from YouTube."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE transcript_jobs
               SET video_title=COALESCE(?, video_title),
                   video_author=COALESCE(?, video_author),
                   updated_at=?
               WHERE job_id=?""",
            (video_title, video_author, now, job_id),
        )


def delete_transcript_job(job_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM transcript_jobs WHERE job_id = ?", (job_id,))
