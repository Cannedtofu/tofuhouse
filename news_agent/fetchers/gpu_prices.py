"""Fetches GPU price index history from api.ornnai.com.

Public API:
  fetch_all_gpu_prices() -> dict[gpu_type, list[{timestamp, index_value}]]
"""

import json
import logging
import urllib.request
from urllib.parse import quote

log = logging.getLogger(__name__)

GPU_TYPES = ["H100 SXM", "H200", "B200", "A100 SXM4", "RTX 5090"]
_BASE_URL = "https://api.ornnai.com/api/gpu/{}/index-history"


def fetch_all_gpu_prices() -> dict:
    """Fetch full price history for all GPU types.

    Returns a dict mapping gpu_type (str) to a list of data points:
        [{"timestamp": "2026-03-04T21:00:00.000Z", "index_value": 1.69}, ...]

    GPU types that fail to fetch are omitted from the result.
    """
    results = {}
    for gpu_type in GPU_TYPES:
        url = _BASE_URL.format(quote(gpu_type))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "news-agent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
            if payload.get("success") and isinstance(payload.get("data"), list):
                results[gpu_type] = payload["data"]
                log.info("GPU prices: %d points for %s", len(payload["data"]), gpu_type)
            else:
                log.warning("GPU prices: unexpected response for %s: %s", gpu_type, payload)
        except Exception as exc:
            log.error("GPU prices: fetch failed for %s: %s", gpu_type, exc)
    return results
