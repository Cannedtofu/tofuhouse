"""
Clean bad Anthropic web-source records.

Default mode is a dry run:
    python scripts/cleanup_anthropic_articles.py

Apply changes:
    python scripts/cleanup_anthropic_articles.py --apply
"""

import argparse
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DB_PATH = Path(__file__).parent.parent / "news.db"
ERROR_PATTERNS = (
    "404 poem",
    "four-zero-four",
    "returned a 404 error",
    "could not be found",
    "no main article body",
    "bad gateway",
)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
TRACKING_QUERY_PREFIXES = ("utm_",)


def canonical_url(url):
    parts = urlsplit((url or "").strip())
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not any(key.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(query_items, doseq=True)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def looks_like_error(content):
    text = (content or "").lower()
    return any(pattern in text for pattern in ERROR_PATTERNS)


def title_is_url(row):
    title = (row["title"] or "").strip()
    return title == row["url"] or title.startswith(("http://", "https://"))


def extract_heading(content):
    for line in (content or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if line.strip().startswith("#") and stripped:
            return stripped
    return ""


def duplicate_score(row):
    content = row["content"] or ""
    score = len(content)
    if not looks_like_error(content):
        score += 1_000_000
    if not title_is_url(row):
        score += 100_000
    return score


def main(apply):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sources = conn.execute(
        """SELECT id, name FROM sources
           WHERE instr(lower(name), 'anthropic') > 0
              OR instr(lower(url), 'anthropic') > 0"""
    ).fetchall()
    if not sources:
        print("No Anthropic source found.")
        return

    source_ids = [row["id"] for row in sources]
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"SELECT * FROM articles WHERE source_id IN ({placeholders})",
        source_ids,
    ).fetchall()

    delete_ids = set()
    title_updates = []
    groups = {}

    for row in rows:
        if looks_like_error(row["content"]):
            delete_ids.add(row["id"])
            continue
        heading = extract_heading(row["content"])
        if heading and title_is_url(row):
            title_updates.append((heading, row["id"]))
        groups.setdefault(canonical_url(row["url"]), []).append(row)

    duplicate_delete_ids = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        keep = max(group, key=duplicate_score)
        for row in group:
            if row["id"] != keep["id"]:
                duplicate_delete_ids.add(row["id"])

    delete_ids |= duplicate_delete_ids
    title_updates = [(title, article_id) for title, article_id in title_updates if article_id not in delete_ids]

    source_label = ", ".join(f"{s['name']}({s['id']})" for s in sources)
    print(f"Sources: {source_label}")
    print(f"Articles scanned: {len(rows)}")
    print(f"Delete error/duplicate articles: {len(delete_ids)}")
    print(f"Fix URL titles: {len(title_updates)}")

    if not apply:
        print("\n[dry-run] No changes made. Re-run with --apply to write changes.")
        return

    for article_id in sorted(delete_ids):
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    for title, article_id in title_updates:
        conn.execute("UPDATE articles SET title = ? WHERE id = ?", (title, article_id))
    conn.commit()
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean bad Anthropic article records.")
    parser.add_argument("--apply", action="store_true", help="Write changes instead of dry-run")
    args = parser.parse_args()
    main(args.apply)