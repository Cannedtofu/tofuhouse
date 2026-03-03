"""
mitmproxy addon — intercepts HTTP responses matching TARGET_URL_PATTERN
and appends the parsed JSON to a JSONL file.

Loaded by mitm_server.py via --scripts flag. Can also be used standalone:
    mitmdump -s proxy/addon.py
"""

import json
import logging
import os
from datetime import datetime, timezone

from mitmproxy import http

import config

logger = logging.getLogger(__name__)


class DataInterceptor:
    """
    mitmproxy addon that filters responses by URL substring and persists
    the JSON body as newline-delimited JSON (JSONL).
    """

    def __init__(
        self,
        url_pattern: str = config.TARGET_URL_PATTERN,
        output_path: str = os.path.join(config.OUTPUT_DIR, config.JSONL_FILENAME),
    ) -> None:
        self.url_pattern = url_pattern
        self.output_path = output_path
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        logger.info(
            "DataInterceptor watching pattern '%s' → %s",
            url_pattern, output_path,
        )

    def response(self, flow: http.HTTPFlow) -> None:
        """Called by mitmproxy for every completed HTTP response."""
        if self.url_pattern not in flow.request.pretty_url:
            return

        raw = flow.response.get_text(strict=False)
        if not raw:
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Non-JSON response from %s: %s", flow.request.pretty_url, exc
            )
            return

        # Attach metadata so each JSONL record is self-contained
        record = {
            "_captured_at": datetime.now(timezone.utc).isoformat(),
            "_url": flow.request.pretty_url,
            "data": data,
        }

        try:
            with open(self.output_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.debug("Saved record from %s", flow.request.pretty_url)
        except OSError as exc:
            logger.error("Failed to write record: %s", exc)


# mitmproxy reads this module-level list when loaded via --scripts
addons = [DataInterceptor()]
