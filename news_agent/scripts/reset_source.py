"""
Delete all articles for a named source and reset its last_fetched timestamp.
Usage:
    .venv\Scripts\python.exe scripts\reset_source.py "semi analysis"
    .venv\Scripts\python.exe scripts\reset_source.py "semi analysis" --dry-run
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "news.db"


def reset_source(name_fragment: str, dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sources = conn.execute(
        "SELECT id, name FROM sources WHERE name LIKE ?",
        (f"%{name_fragment}%",),
    ).fetchall()

    if not sources:
        print(f"No source matching '{name_fragment}' found.")
        conn.close()
        sys.exit(1)

    for s in sources:
        count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE source_id = ?", (s["id"],)
        ).fetchone()[0]
        print(f"  Source: '{s['name']}' (id={s['id']})  —  {count} article(s)")

    if dry_run:
        print("\n[dry-run] No changes made.")
        conn.close()
        return

    if len(sources) > 1:
        confirm = input(f"\n{len(sources)} sources matched. Delete all? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            conn.close()
            return

    for s in sources:
        conn.execute("DELETE FROM articles WHERE source_id = ?", (s["id"],))
        conn.execute("UPDATE sources SET last_fetched = NULL WHERE id = ?", (s["id"],))
        print(f"  Cleared articles and reset last_fetched for '{s['name']}'")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset articles for a source.")
    parser.add_argument("source", help="Partial source name to match (case-insensitive)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without doing it")
    args = parser.parse_args()
    reset_source(args.source, dry_run=args.dry_run)
