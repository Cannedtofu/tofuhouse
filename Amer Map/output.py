import os
import re
import pandas as pd
from datetime import date
from typing import Dict, List, Optional


def _quarter_label(d: date = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _output_filename(label: Optional[str] = None) -> str:
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
    """Compare new vs old stores for a brand.

    Returns change records that retain ALL original columns from the source row,
    with metadata columns (brand, change_type, changed_fields, old_name,
    old_address) prepended.  NEW/CHANGED records use the new-quarter row;
    CLOSED records use the old-quarter row (last known state).
    """
    new_df = new_df.copy().reset_index(drop=True)
    old_df = old_df.copy().reset_index(drop=True)
    new_df["_id_str"] = new_df["id"].astype(str)
    old_df["_id_str"] = old_df["id"].astype(str)

    new_ids = set(new_df["_id_str"]) - {"nan"}
    old_ids = set(old_df["_id_str"]) - {"nan"}

    changes = []

    # NEW — full row from new_df
    for sid in new_ids - old_ids:
        row = new_df[new_df["_id_str"] == sid].iloc[0].drop("_id_str").to_dict()
        row.update({"brand": brand_name, "change_type": "NEW",
                    "changed_fields": "", "old_name": "", "old_address": ""})
        changes.append(row)

    # CLOSED — full row from old_df (last known state)
    for sid in old_ids - new_ids:
        row = old_df[old_df["_id_str"] == sid].iloc[0].drop("_id_str").to_dict()
        row.update({"brand": brand_name, "change_type": "CLOSED",
                    "changed_fields": "", "old_name": "", "old_address": ""})
        changes.append(row)

    # CHANGED — full row from new_df + old_name/old_address for reference
    watch = ["name", "address", "country"]
    for sid in new_ids & old_ids:
        new_row = new_df[new_df["_id_str"] == sid].iloc[0]
        old_row = old_df[old_df["_id_str"] == sid].iloc[0]
        diffs = [f for f in watch if f in new_row.index and f in old_row.index
                 and str(new_row[f]) != str(old_row[f])]
        if diffs:
            row = new_row.drop("_id_str").to_dict()
            row.update({
                "brand": brand_name,
                "change_type": "CHANGED",
                "changed_fields": ", ".join(diffs),
                "old_name": old_row.get("name", ""),
                "old_address": old_row.get("address", ""),
            })
            changes.append(row)

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
    prev_xl = None

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
            meta_cols = ["brand", "change_type", "changed_fields", "old_name", "old_address"]
            rest_cols = [c for c in changes_df.columns if c not in meta_cols]
            changes_df = changes_df[meta_cols + rest_cols]
            changes_df.to_excel(writer, sheet_name="Changes", index=False)
            print(f"  Changes sheet written: {len(all_changes)} total changes")
        elif prev_file:
            pd.DataFrame([{"note": "No changes detected vs previous quarter"}]).to_excel(
                writer, sheet_name="Changes", index=False
            )

    print(f"\n✅ Output written: {output_file}")
    return output_file
