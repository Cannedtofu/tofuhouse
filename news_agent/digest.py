"""Build a markdown digest from articles stored in the database."""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

import db


def build_digest(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_ids: Optional[list[int]] = None,
) -> str:
    """
    Build a markdown-formatted digest.
    date_from / date_to: ISO date strings like '2026-04-10' (defaults to today).
    source_ids: list of source IDs to include; None means all.
    """
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date.today().isoformat()

    articles = db.get_articles(
        date_from=date_from,
        date_to=date_to,
        source_ids=source_ids,
    )

    if not articles:
        return f"# News Digest — {date_from} to {date_to}\n\nNo articles found for the selected period.\n"

    # Group by source
    grouped: dict[str, list] = {}
    for a in articles:
        source_name = a["source_name"]
        grouped.setdefault(source_name, []).append(a)

    lines = [f"# News Digest — {date_from} to {date_to}\n"]
    lines.append(f"*{len(articles)} article(s) from {len(grouped)} source(s)*\n")

    for source_name, items in grouped.items():
        lines.append(f"\n## {source_name}\n")
        for a in items:
            title = a["title"] or a["url"]
            url = a["url"]
            summary = a["summary"] or "*(summary pending)*"
            pub = a["published_at"] or a["fetched_at"] or ""
            if pub:
                try:
                    dt = datetime.fromisoformat(pub)
                    pub = dt.strftime("%b %d, %Y")
                except Exception:
                    pass
            lines.append(f"**[{title}]({url})**" + (f"  ·  {pub}" if pub else ""))
            lines.append(f"{summary}\n")

    return "\n".join(lines)
