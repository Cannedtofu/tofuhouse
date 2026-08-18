"""Local smoke test for Q4 Inc attendee audio extraction.

Usage:
  python scripts/q4inc_debug_audio.py "https://events.q4inc.com/attendee/575045483"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from q4inc_audio import extract_q4inc_audio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Q4 Inc attendee URL")
    parser.add_argument("--headed", action="store_true", help="Show the browser while debugging")
    parser.add_argument("--hold-seconds", type=int, default=0, help="Keep the browser open before closing")
    parser.add_argument("--slow-ms", type=int, default=0, help="Slow down Playwright actions for visual debugging")
    parser.add_argument("--output-dir", default=os.path.join("audio_cache", "q4inc"))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = extract_q4inc_audio(
        args.url,
        output_dir=args.output_dir,
        headless=not args.headed,
        slow_mo_ms=args.slow_ms,
        hold_seconds=args.hold_seconds,
    )
    print(json.dumps({
        "title": result.title,
        "page_url": result.page_url,
        "media_url": result.media_url,
        "output_path": result.output_path,
        "candidate_count": len(result.captured_urls),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
