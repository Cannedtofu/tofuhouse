"""
CLI orchestrator: fetch all sources → summarize → build digest → send email.
Run daily via GitHub Actions or a local cron job.
"""

import logging
import sys
from datetime import date

import db
from digest import build_digest
from email_sender import send_digest
from pipeline import run_fetch_and_summarize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== News Agent starting ===")
    db.init_db()

    result = run_fetch_and_summarize(summarize=True)
    for s in result["sources"]:
        status = f"error: {s['error']}" if s["error"] else f"{s['fetched']} fetched, {s['new']} new"
        logger.info("  [%s] %s", s["name"], status)
    logger.info("Fetch complete: %d total new articles.", result["total_new"])

    digest_md = build_digest()
    if not digest_md.strip():
        logger.warning("Digest is empty — nothing to send.")
        sys.exit(0)

    ok = send_digest(digest_md, date_label=date.today().isoformat())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
