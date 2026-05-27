"""Database operations for users, source follows, and digest preferences."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


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


def get_all_users() -> list[sqlite3.Row]:
    """Return all users with their digest settings, ordered by email."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT id, email, digest_enabled, digest_frequency_days, digest_last_sent, last_seen
               FROM users ORDER BY email"""
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
# Source follows
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
# Digest preferences
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
