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

import pandas as pd

JSONL_PATH = os.path.join("output", "results.jsonl")
EXCEL_PATH = os.path.join("output", "results.xlsx")


def main() -> None:
    rows: list[dict] = []
    seen_ids: set[str] = set()

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
                rows.append(item)

    if not rows:
        print("No items found in results.jsonl.")
        return

    df = pd.json_normalize(rows)
    df.to_excel(EXCEL_PATH, index=False, engine="openpyxl")
    print(f"Exported {len(df)} rows → {EXCEL_PATH}")


if __name__ == "__main__":
    main()
