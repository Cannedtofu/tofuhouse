"""SQLite connection management and schema initialisation."""

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
                type         TEXT    NOT NULL CHECK(type IN ('rss','nitter','web','youtube')),
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

            CREATE TABLE IF NOT EXISTS digest_presets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name            TEXT    NOT NULL,
                source_ids_json TEXT    NOT NULL DEFAULT '[]',
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_digest_presets_user ON digest_presets(user_id);

            CREATE TABLE IF NOT EXISTS gpu_price_cache (
                gpu_type   TEXT PRIMARY KEY,
                data_json  TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gpu_price_history (
                gpu_type    TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                index_value REAL NOT NULL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (gpu_type, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_gpu_price_history_gpu
                ON gpu_price_history(gpu_type, timestamp);

            CREATE TABLE IF NOT EXISTS script_reports (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                script_name             TEXT    NOT NULL UNIQUE,
                status                  TEXT    NOT NULL CHECK(status IN ('ok','error')),
                error_message           TEXT,
                data_json               TEXT,
                pushed_at               TEXT    NOT NULL,
                expected_interval_hours REAL    NOT NULL DEFAULT 24
            );

            CREATE TABLE IF NOT EXISTS script_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                script_name TEXT    NOT NULL UNIQUE,
                filename    TEXT    NOT NULL,
                file_data   BLOB    NOT NULL,
                uploaded_at TEXT    NOT NULL
            );
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
        try:
            conn.execute("ALTER TABLE transcript_jobs ADD COLUMN transcript_zh TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE transcript_jobs ADD COLUMN audio_path TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE transcript_jobs ADD COLUMN initiated_by TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN translated_content TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE digest_presets ADD COLUMN digest_enabled INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE digest_presets ADD COLUMN digest_frequency_days INTEGER NOT NULL DEFAULT 7")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE digest_presets ADD COLUMN digest_last_sent TEXT")
        except Exception:
            pass
        # Widen sources.type CHECK to include 'youtube' and 'xiaoyuzhou'
        _src_check = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
        ).fetchone()
        if _src_check and "'bilibili'" not in _src_check[0]:
            conn.executescript("""
                PRAGMA foreign_keys=OFF;
                CREATE TABLE sources_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT    NOT NULL,
                    type         TEXT    NOT NULL CHECK(type IN ('rss','nitter','web','youtube','xiaoyuzhou','bilibili')),
                    url          TEXT    NOT NULL UNIQUE,
                    url_filter   TEXT,
                    last_fetched TEXT
                );
                INSERT INTO sources_new SELECT id, name, type, url, url_filter, last_fetched
                    FROM sources;
                DROP TABLE sources;
                ALTER TABLE sources_new RENAME TO sources;
                PRAGMA foreign_keys=ON;
            """)
        # One-time migration: copy blob data from gpu_price_cache → gpu_price_history
        # Safe to run repeatedly — INSERT OR IGNORE skips already-migrated rows.
        try:
            import json as _json
            _cache_rows = conn.execute(
                "SELECT gpu_type, data_json, fetched_at FROM gpu_price_cache"
            ).fetchall()
            for _row in _cache_rows:
                _points = _json.loads(_row["data_json"])
                for _pt in _points:
                    conn.execute(
                        """INSERT OR IGNORE INTO gpu_price_history
                               (gpu_type, timestamp, index_value, fetched_at)
                           VALUES (?, ?, ?, ?)""",
                        (_row["gpu_type"], _pt["timestamp"], _pt["index_value"], _row["fetched_at"]),
                    )
        except Exception:
            pass  # gpu_price_cache may be empty or not yet exist — safe to skip
