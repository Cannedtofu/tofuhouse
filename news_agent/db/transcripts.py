"""Database operations for YouTube transcript jobs."""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.core import get_conn


def create_transcript_job(
    video_url: str,
    video_id: str,
    mode: str = "no_diarization",
    initiated_by: Optional[str] = None,
) -> str:
    """Insert a new transcript job with status 'pending'. Returns the job_id (UUID)."""
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transcript_jobs
               (job_id, video_url, video_id, mode, status, initiated_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (job_id, video_url, video_id, mode, initiated_by, now, now),
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
            """SELECT job_id, video_id, video_url, video_title, video_author,
                      mode, status, initiated_by, created_at
               FROM transcript_jobs
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def update_transcript_job(
    job_id: str,
    status: str,
    transcript: Optional[str] = None,
    transcript_zh: Optional[str] = None,
    summary: Optional[str] = None,
    error_message: Optional[str] = None,
    audio_path: Optional[str] = None,
):
    """Update a transcript job's status and optional result fields."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE transcript_jobs
               SET status=?, transcript=COALESCE(?, transcript),
                   transcript_zh=COALESCE(?, transcript_zh),
                   summary=COALESCE(?, summary),
                   error_message=COALESCE(?, error_message),
                   audio_path=COALESCE(?, audio_path),
                   updated_at=?
               WHERE job_id=?""",
            (status, transcript, transcript_zh, summary, error_message, audio_path, now, job_id),
        )


def clear_transcript_summary(job_id: str) -> None:
    """Set summary to NULL so it can be regenerated."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE transcript_jobs SET summary=NULL, updated_at=? WHERE job_id=?",
            (now, job_id),
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
