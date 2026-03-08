import json
import os
from datetime import datetime
import pandas as pd

JSONL_PATH = os.path.join("output", "results.jsonl")
EXCEL_PATH = os.path.join("output", "sample_results_300.xlsx")

def main() -> None:
    rows: list[dict] = []
    seen_ids: set[str] = set()

    if not os.path.exists(JSONL_PATH):
        print(f"Error: {JSONL_PATH} not found.")
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
                if len(rows) >= 300:
                    break
                    
                item_id = item.get("id")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                
                # Add the current date to track when this data was generated
                item['query_date'] = datetime.now().strftime("%Y-%m-%d")
                
                rows.append(item)
                
            if len(rows) >= 300:
                break

    if not rows:
        print("No items found in results.jsonl.")
        return

    df = pd.json_normalize(rows)
    df.to_excel(EXCEL_PATH, index=False, engine="openpyxl")
    print(f"Exported {len(df)} rows → {EXCEL_PATH}")

if __name__ == "__main__":
    main()
