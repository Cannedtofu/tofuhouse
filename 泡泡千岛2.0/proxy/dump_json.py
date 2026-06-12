"""Temporary diagnostic addon — captures first spus/feed response to a JSON file. Delete after use."""
import json
import os
import logging

logger = logging.getLogger(__name__)

class SpusFeedCapture:
    def __init__(self):
        self._saved = False

    def response(self, flow):
        if self._saved:
            return
        if "spus/feed" in flow.request.pretty_url:
            raw = flow.response.get_text(strict=False)
            try:
                obj = json.loads(raw)
                os.makedirs("output", exist_ok=True)
                with open("output/spus_feed_sample.json", "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
                self._saved = True
                logger.info("=== WRITTEN to output/spus_feed_sample.json ===")
                logger.info("Top-level keys: %s", list(obj.keys()))
                data_val = obj.get("data")
                if isinstance(data_val, dict):
                    logger.info("payload['data'] keys: %s", list(data_val.keys()))
            except Exception as e:
                logger.error("Parse error: %s", e)

addons = [SpusFeedCapture()]
