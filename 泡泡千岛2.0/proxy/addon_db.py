import os
import json
import sqlite3
import logging
from datetime import datetime

# Ensure config is available
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)

class DataInterceptor:
    """
    mitmproxy addon that filters responses and stores them in memory.
    Saves to SQLite ONLY when a clear success signal is received.
    """

    def __init__(
        self,
        url_pattern: str = config.TARGET_URL_PATTERN,
        db_path: str = os.path.join(config.OUTPUT_DIR, "results.db"),
    ) -> None:
        self.url_pattern = url_pattern
        self.db_path = db_path
        # Historical DB stores everything ever found in MuMu
        self.history_db_path = os.path.join(config.OUTPUT_DIR, "results_history.db")
        self._captured_items = {} 
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        logger.info(
            "DataInterceptor (Buffered) watching pattern '%s'",
            url_pattern
        )

    def response(self, flow):
        """Processes responses and buffers them in memory."""
        # 1. Look for the target data feed
        if self.url_pattern in flow.request.pretty_url:
            raw = flow.response.get_text(strict=False)
            if not raw: return
            try:
                payload = json.loads(raw)
                items = payload.get("data", {}).get("list", [])
                if not items:
                    # Log the top-level keys and one level deeper so we can identify
                    # the correct path if the API response structure has changed.
                    top_keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
                    data_val = payload.get("data") if isinstance(payload, dict) else None
                    data_keys = list(data_val.keys()) if isinstance(data_val, dict) else repr(data_val)[:120]
                    logger.warning(
                        "0 items parsed from %s — top-level keys: %s | payload['data'] keys: %s",
                        flow.request.pretty_url, top_keys, data_keys,
                    )
                for item in items:
                    item_id = str(item.get("id"))
                    self._captured_items[item_id] = item
                logger.info(f"Buffered {len(items)} items in memory. Total unique items: {len(self._captured_items)}")
            except Exception as e:
                logger.warning(f"Failed to buffer items: {e}")
                
        # 2. Total Count Polling Endpoint
        if "mitm_action=get_count" in flow.request.pretty_url:
            count = len(self._captured_items)
            flow.response = flow.response.make(200, str(count).encode())
            return

        # 3. Look for the "COMMIT" signal from main.py
        if "mitm_action=commit_success" in flow.request.pretty_url:
            self._commit_to_db()
            flow.response = flow.response.make(200, b"DB Commit Successful")

    def _commit_to_db(self):
        """Write all buffered items to results.db (fresh) and results_history.db (append)."""
        if not self._captured_items:
            logger.warning("Commit signal received but no items were captured.")
            return

        logger.info(f"Success signal received. Persisting {len(self._captured_items)} items...")
        
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Part A: results.db (cumulative — upsert on id)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS feed_results (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    mainTagDisplayName TEXT,
                    query_date TEXT,
                    raw_json TEXT
                )
            ''')
            for item_id, item in self._captured_items.items():
                name = item.get("name")
                main_tag = item.get("mainTagDisplayName", "N/A")
                cursor.execute('''
                    INSERT OR REPLACE INTO feed_results (id, name, mainTagDisplayName, query_date, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                ''', (item_id, name, main_tag, current_date, json.dumps(item, ensure_ascii=False)))
            conn.commit()
            conn.close()
            logger.info(f"Updated {self.db_path} with {len(self._captured_items)} items (cumulative).")
        except Exception as e:
            logger.error(f"Failed to update results.db: {e}")

        # Part B: Persistent results_history.db (Archive)
        try:
            conn_hist = sqlite3.connect(self.history_db_path)
            cursor_hist = conn_hist.cursor()
            cursor_hist.execute('''
                CREATE TABLE IF NOT EXISTS historical_results (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    mainTagDisplayName TEXT,
                    first_found TEXT,
                    last_seen TEXT,
                    raw_json TEXT
                )
            ''')
            for item_id, item in self._captured_items.items():
                name = item.get("name")
                main_tag = item.get("mainTagDisplayName", "N/A")
                # INSERT OR IGNORE, followed by an UPDATE to last_seen
                cursor_hist.execute('''
                    INSERT OR IGNORE INTO historical_results 
                    (id, name, mainTagDisplayName, first_found, last_seen, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (item_id, name, main_tag, current_date, current_date, json.dumps(item, ensure_ascii=False)))
                cursor_hist.execute('UPDATE historical_results SET last_seen = ? WHERE id = ?', (current_date, item_id))
            
            conn_hist.commit()
            conn_hist.close()
            logger.info(f"Updated {self.history_db_path} with ARCHIVE data.")
        except Exception as e:
            logger.error(f"Failed to update results_history.db: {e}")

        # Clear memory after successful write
        self._captured_items.clear()

addons = [DataInterceptor()]
