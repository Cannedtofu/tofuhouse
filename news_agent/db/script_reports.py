"""DB operations for external script reports and file uploads."""

import json
from datetime import datetime, timezone

from db.core import get_conn


def upsert_script_report(
    script_name: str,
    status: str,
    error_message: str | None,
    data_json: str | None,
    expected_interval_hours: float,
) -> None:
    """Insert or replace the latest report for a script."""
    pushed_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_reports
                   (script_name, status, error_message, data_json, pushed_at, expected_interval_hours)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(script_name) DO UPDATE SET
                   status                  = excluded.status,
                   error_message           = excluded.error_message,
                   data_json               = excluded.data_json,
                   pushed_at               = excluded.pushed_at,
                   expected_interval_hours = excluded.expected_interval_hours""",
            (script_name, status, error_message, data_json, pushed_at, expected_interval_hours),
        )


def get_all_script_reports() -> list[dict]:
    """Return all script reports with is_overdue flag and parsed panels."""
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM script_reports ORDER BY script_name"
        ).fetchall()
    result = []
    for r in rows:
        pushed_at = datetime.fromisoformat(r["pushed_at"])
        if pushed_at.tzinfo is None:
            pushed_at = pushed_at.replace(tzinfo=timezone.utc)
        hours_since = (now - pushed_at).total_seconds() / 3600
        is_overdue = hours_since > r["expected_interval_hours"]
        panels = json.loads(r["data_json"]) if r["data_json"] else []
        result.append({
            "script_name":             r["script_name"],
            "status":                  r["status"],
            "error_message":           r["error_message"],
            "panels":                  panels,
            "pushed_at":               r["pushed_at"],
            "expected_interval_hours": r["expected_interval_hours"],
            "is_overdue":              is_overdue,
        })
    return result


def upsert_script_file(script_name: str, filename: str, file_data: bytes) -> None:
    """Store (or replace) the latest Excel file for a script."""
    uploaded_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_files (script_name, filename, file_data, uploaded_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(script_name) DO UPDATE SET
                   filename    = excluded.filename,
                   file_data   = excluded.file_data,
                   uploaded_at = excluded.uploaded_at""",
            (script_name, filename, file_data, uploaded_at),
        )


def get_script_file(script_name: str) -> dict | None:
    """Return the stored file for a script, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT filename, file_data, uploaded_at FROM script_files WHERE script_name = ?",
            (script_name,),
        ).fetchone()
    if not row:
        return None
    return {"filename": row["filename"], "file_data": row["file_data"], "uploaded_at": row["uploaded_at"]}


def get_scripts_with_files() -> set[str]:
    """Return set of script names that have an uploaded file."""
    with get_conn() as conn:
        rows = conn.execute("SELECT script_name FROM script_files").fetchall()
    return {r["script_name"] for r in rows}


# ---------------------------------------------------------------------------
# Panel access control (admin toggles per-panel visibility for non-admins)
# ---------------------------------------------------------------------------

def get_panel_access() -> dict:
    """Return {panel_key: public} for all panels with a stored preference.

    Panels not in the table default to public=True (visible to everyone).
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT panel_key, public FROM panel_access").fetchall()
    return {r["panel_key"]: bool(r["public"]) for r in rows}


def set_panel_access(panel_key: str, public: bool) -> None:
    """Upsert the visibility setting for a panel."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO panel_access (panel_key, public)
               VALUES (?, ?)
               ON CONFLICT(panel_key) DO UPDATE SET public = excluded.public""",
            (panel_key, 1 if public else 0),
        )
