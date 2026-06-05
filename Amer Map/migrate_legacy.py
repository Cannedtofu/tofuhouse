import os
import sys
import pandas as pd

# Fix encoding for Windows console output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Maps quarter label → legacy files to include.
# Only files with the locally.com API schema (columns: id, name, lat, lng, ...) are included.
# Excluded: brand store pages, analyzed summaries — they have no 'id' column.
LEGACY = {
    "2025-Q1": {
        "folder": "202503",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Hoka":      "store_data_hoka.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
        },
        "drop_cols": ["date"],  # 202503 Arc'teryx has an extra 'date' column to strip
    },
    "2025-Q4": {
        "folder": "202512",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Hoka":      "store_data_hoka.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
        },
        "drop_cols": [],
    },
    "2026-Q1": {
        "folder": "202603",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
            # Hoka not available for this quarter — omitted intentionally
        },
        "drop_cols": [],
    },
}


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["id"], keep="first").reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"    dedup: {before} → {after} rows ({before - after} removed)")
    return df


def migrate():
    for quarter, config in LEGACY.items():
        output_file = f"store_data_{quarter}.xlsx"
        if os.path.exists(output_file):
            print(f"[SKIP] {output_file} already exists")
            continue

        print(f"\nMigrating {quarter} → {output_file}")
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            for sheet_name, filename in config["brands"].items():
                path = os.path.join(config["folder"], filename)
                if not os.path.exists(path):
                    print(f"  [WARN] {path} not found — skipping {sheet_name}")
                    continue
                df = pd.read_excel(path)
                print(f"  {sheet_name}: {len(df)} raw rows")
                for col in config.get("drop_cols", []):
                    if col in df.columns:
                        df = df.drop(columns=[col])
                        print(f"    dropped column: {col}")
                df = deduplicate(df)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  {sheet_name}: written {len(df)} unique stores")
        print(f"[OK] {output_file} written")


if __name__ == "__main__":
    migrate()
