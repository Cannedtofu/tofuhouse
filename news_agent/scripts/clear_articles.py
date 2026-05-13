"""Delete all articles from news.db without touching sources or fetch_log."""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    if count == 0:
        print("No articles in database — nothing to do.")
        conn.close()
        return

    print(f"This will permanently delete {count} article(s) from {DB_PATH}.")
    answer = input("Confirm? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        conn.close()
        return

    conn.execute("DELETE FROM articles")
    conn.execute("UPDATE sources SET last_fetched = NULL")
    conn.commit()
    conn.close()
    print(f"Deleted {count} article(s). Sources and fetch log are intact.")


if __name__ == "__main__":
    main()
