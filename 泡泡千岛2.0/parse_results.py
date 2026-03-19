"""
Parse output/results.jsonl → output/results.xlsx

Each JSONL line wraps an API response:
    {"_captured_at": "...", "_url": "...", "data": {"code": 0, "data": {"list": [...]}}}

This script extracts every item from the nested "list", deduplicates by id,
and writes a flat Excel file using pd.json_normalize (which expands nested
dicts like mainTag and flags into dot-notation columns).

Usage:
    python parse_results.py
"""

import json
import os
import sqlite3
from datetime import datetime

import pandas as pd

JSONL_PATH = os.path.join("output", "results.jsonl")
DB_PATH = os.path.join("output", "results.db")

def main() -> None:
    rows: list[dict] = []
    seen_ids: set[str] = set()

    if not os.path.exists(JSONL_PATH):
        print(f"No results file found at {JSONL_PATH}.")
        return

    with open(JSONL_PATH, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"Skipping malformed line {lineno}: {exc}")
                continue

            items = record.get("data", {}).get("data", {}).get("list", [])
            for item in items:
                item_id = item.get("id")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                
                # Add the current date to track when this data was generated
                item['query_date'] = datetime.now().strftime("%Y-%m-%d")
                
                rows.append(item)

    if not rows:
        print("No items found in results.jsonl.")
        return

    df = pd.json_normalize(rows)
    
    # Save to SQLite
    conn = sqlite3.connect(DB_PATH)
    # Ensure specific columns exist if needed or let to_sql handle it
    df.to_sql("feed_results", conn, if_exists="replace", index=False)
    conn.close()
    
    print(f"Exported {len(df)} rows → {DB_PATH}")

if __name__ == "__main__":
    main()
