"""Fetch Silicon Data's public LLM Token Expenditure Index snapshot.

Public API:
  run_llm_token_expenditure_index_fetch() -> (panels: list[dict], excel_bytes: bytes)

Silicon Data's public embed currently exposes the latest index print and its
"As of" date, but not the full historical series. To make the dashboard
useful over time, this fetcher stores one row per published source date in the
DB-backed Excel file and rebuilds a cumulative local history on every run.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import datetime

import openpyxl
import requests

import db

log = logging.getLogger(__name__)

_EMBED_URL = "https://portal.silicondata.com/token-index-chart"
_SCRIPT_NAME = "LLM Token Expenditure Index"
_RAW_HEADERS = ["As Of Date", "Index Value USD / 1M Tokens", "Fetched At UTC", "Source URL"]


def fetch_snapshot_series() -> list[dict]:
    resp = requests.get(
        _EMBED_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    return parse_snapshot_series(resp.text)


def parse_snapshot_series(html: str) -> list[dict]:
    series_match = re.search(
        r'\\"indexes\\":\{([^{}]+)\}',
        html,
    )
    if not series_match:
        raise ValueError("Could not parse Silicon Data token index series")

    fetched_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    series_json = "{" + series_match.group(1).replace('\\"', '"') + "}"
    indexes = json.loads(series_json)
    rows = []
    for as_of_date, value in sorted(indexes.items()):
        rows.append({
            "as_of_date": as_of_date,
            "index_value": float(value),
            "fetched_at": fetched_at,
            "source_url": _EMBED_URL,
        })
    return rows


def _load_existing_rows(wb) -> dict[str, dict]:
    if "Daily Index" not in wb.sheetnames:
        return {}

    ws = wb["Daily Index"]
    existing: dict[str, dict] = {}
    for row_idx in range(2, ws.max_row + 1):
        as_of_date = ws.cell(row=row_idx, column=1).value
        index_value = ws.cell(row=row_idx, column=2).value
        fetched_at = ws.cell(row=row_idx, column=3).value
        source_url = ws.cell(row=row_idx, column=4).value
        if not as_of_date or index_value is None:
            continue
        existing[str(as_of_date)] = {
            "as_of_date": str(as_of_date),
            "index_value": float(index_value),
            "fetched_at": str(fetched_at or ""),
            "source_url": str(source_url or _EMBED_URL),
        }
    return existing


def merge_rows(wb, snapshots: list[dict]) -> list[dict]:
    merged = _load_existing_rows(wb)
    for snapshot in snapshots:
        prior = merged.get(snapshot["as_of_date"])
        if prior is not None and prior["index_value"] != snapshot["index_value"]:
            log.info(
                "LLM token expenditure index: %s revised by source - %s -> %s",
                snapshot["as_of_date"],
                prior["index_value"],
                snapshot["index_value"],
            )
        merged[snapshot["as_of_date"]] = snapshot
    return [merged[key] for key in sorted(merged.keys())]


def _upsert_raw_sheet(wb, rows: list[dict]) -> None:
    if "Daily Index" not in wb.sheetnames:
        ws = wb.create_sheet("Daily Index")
    else:
        ws = wb["Daily Index"]

    if ws.max_row > 0:
        ws.delete_rows(1, ws.max_row)
    ws.append(_RAW_HEADERS)
    for row in rows:
        ws.append([
            row["as_of_date"],
            row["index_value"],
            row["fetched_at"],
            row["source_url"],
        ])


def build_panels(rows: list[dict]) -> list[dict]:
    history_rows = [
        [row["as_of_date"], f'{row["index_value"]:.4f}']
        for row in reversed(rows)
    ]
    return [
        {
            "type": "line",
            "title": "LLM Token Expenditure Index",
            "x_type": "date",
            "span_gaps": True,
            "datasets": [
                {
                    "label": "USD / 1M Tokens",
                    "data": [
                        {"x": row["as_of_date"], "y": row["index_value"]}
                        for row in rows
                    ],
                }
            ],
        },
        {
            "type": "table",
            "title": "Published History",
            "headers": ["Date", "USD / 1M Tokens"],
            "rows": history_rows,
        },
    ]


def run_llm_token_expenditure_index_fetch() -> tuple[list[dict], bytes]:
    snapshots = fetch_snapshot_series()
    if not snapshots:
        raise ValueError("Silicon Data token index returned no rows")
    log.info(
        "LLM token expenditure index: fetched %d row(s) from %s to %s",
        len(snapshots),
        snapshots[0]["as_of_date"],
        snapshots[-1]["as_of_date"],
    )

    existing_file = db.get_script_file(_SCRIPT_NAME)
    if existing_file:
        wb = openpyxl.load_workbook(io.BytesIO(existing_file["file_data"]))
    else:
        wb = openpyxl.Workbook()

    merged_rows = merge_rows(wb, snapshots)
    _upsert_raw_sheet(wb, merged_rows)

    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]

    buf = io.BytesIO()
    wb.save(buf)
    excel_bytes = buf.getvalue()

    panels = build_panels(merged_rows)
    return panels, excel_bytes
