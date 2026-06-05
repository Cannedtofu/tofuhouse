import os
import re
import pandas as pd
from datetime import date
from typing import Dict, List, Optional


def _quarter_label(d: date = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _output_filename(label: str = None) -> str:
    label = label or _quarter_label()
    return f"store_data_{label}.xlsx"


def _find_previous_file(current_label: str) -> Optional[str]:
    """Return path to the most recent store_data_*.xlsx that is not the current one."""
    pattern = re.compile(r"store_data_(\d{4}-Q\d)\.xlsx")
    candidates = []
    for fname in os.listdir("."):
        m = pattern.match(fname)
        if m and m.group(1) != current_label:
            candidates.append((m.group(1), fname))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _detect_changes(new_df: pd.DataFrame, old_df: pd.DataFrame, brand_name: str) -> List[dict]:
    """Compare new vs old stores for a brand. Returns list of change records."""
    new_ids = set(new_df["id"].dropna().astype(str))
    old_ids = set(old_df["id"].dropna().astype(str))

    new_df = new_df.copy()
    old_df = old_df.copy()
    new_df["_id_str"] = new_df["id"].astype(str)
    old_df["_id_str"] = old_df["id"].astype(str)

    changes = []

    for sid in new_ids - old_ids:
        row = new_df[new_df["_id_str"] == sid].iloc[0]
        changes.append({
            "brand": brand_name,
            "change_type": "NEW",
            "id": sid,
            "name": row.get("name"),
            "address": row.get("address"),
            "city": row.get("city"),
            "country": row.get("country"),
        })

    for sid in old_ids - new_ids:
        row = old_df[old_df["_id_str"] == sid].iloc[0]
        changes.append({
            "brand": brand_name,
            "change_type": "CLOSED",
            "id": sid,
            "name": row.get("name"),
            "address": row.get("address"),
            "city": row.get("city"),
            "country": row.get("country"),
        })

    for sid in new_ids & old_ids:
        new_row = new_df[new_df["_id_str"] == sid].iloc[0]
        old_row = old_df[old_df["_id_str"] == sid].iloc[0]
        watch = ["name", "address", "country"]
        diffs = [f for f in watch if str(new_row.get(f, "")) != str(old_row.get(f, ""))]
        if diffs:
            changes.append({
                "brand": brand_name,
                "change_type": "CHANGED",
                "id": sid,
                "name": new_row.get("name"),
                "address": new_row.get("address"),
                "city": new_row.get("city"),
                "country": new_row.get("country"),
                "changed_fields": ", ".join(diffs),
                "old_name": old_row.get("name"),
                "old_address": old_row.get("address"),
            })

    return changes


def write_output(brand_data: Dict[str, List[dict]]) -> str:
    """
    Write one Excel file with one sheet per brand + a Changes sheet.
    brand_data: {sheet_name: [store_dict, ...]}
    Returns the output filename.
    """
    label = _quarter_label()
    output_file = _output_filename(label)
    prev_file = _find_previous_file(label)

    all_changes = []

    if prev_file:
        print(f"  Change detection: comparing against {prev_file}")
        prev_xl = pd.ExcelFile(prev_file)

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for sheet_name, stores in brand_data.items():
            df = pd.DataFrame(stores)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            if prev_file and sheet_name in prev_xl.sheet_names:
                old_df = prev_xl.parse(sheet_name)
                changes = _detect_changes(df, old_df, sheet_name)
                all_changes.extend(changes)
                print(f"    {sheet_name}: {len(changes)} changes detected")

        if all_changes:
            changes_df = pd.DataFrame(all_changes)
            changes_df.to_excel(writer, sheet_name="Changes", index=False)
            print(f"  Changes sheet written: {len(all_changes)} total changes")
        elif prev_file:
            pd.DataFrame([{"note": "No changes detected vs previous quarter"}]).to_excel(
                writer, sheet_name="Changes", index=False
            )

    print(f"\n✅ Output written: {output_file}")
    return output_file
