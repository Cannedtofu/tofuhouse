# OpenRouter Weekly Usage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone Python script (`openrouter_usage.py`) that pulls weekly LLM token-usage data from OpenRouter's public JSON API, accumulates it permanently in Excel, and pushes two chart panels (total usage, week-over-week change) to the existing news_agent dashboard.

**Architecture:** Pure-Python script (no Selenium) following the GAODE.py integration pattern — own folder/venv, file-based logging, pushes via the existing generic `/api/report` + `/api/report/<script>/excel` endpoints. One small dashboard.html fix is required: the `x_type: "date"` chart rendering path has never been exercised before (GAODE uses `day_of_year`) and is missing a proper Chart.js time-axis config.

**Tech Stack:** Python 3.11+, `requests`, `openpyxl`, `pytest` (script side, own venv). No changes to news_agent's Python dependencies — only a Jinja/JS template fix.

---

## Reference: spec doc

Full design rationale lives in `docs/superpowers/specs/2026-06-28-openrouter-usage-design.md`. Read it if anything here is ambiguous — this plan implements that spec exactly.

## File Structure

- Create: `D:\代码项目\OpenRouterUsage\openrouter_usage.py` — main script, all logic
- Create: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py` — pytest unit tests for the pure-logic functions (parsing, delta math, panel building, Excel upsert)
- Create: `D:\代码项目\OpenRouterUsage\requirements.txt` — `requests`, `openpyxl`, `pytest`
- Modify: `D:\代码项目\news_agent\templates\dashboard.html:574-577` — add `type: 'time'` to the x-axis scale for non-`day_of_year` script panels
- Modify: `D:\代码项目\news_agent\docs\dashboard-integration-guide.md` — add a reference-table row for `openrouter_usage`

No changes needed to `app.py`, `db/`, or any news_agent Python dependency — the existing generic panel/report endpoints already support everything this script needs.

---

### Task 1: Project scaffolding

**Files:**
- Create: `D:\代码项目\OpenRouterUsage\requirements.txt`

- [ ] **Step 1: Create the project folder and venv**

```bash
mkdir -p "D:\代码项目\OpenRouterUsage"
cd "D:\代码项目\OpenRouterUsage"
python -m venv venv
```

- [ ] **Step 2: Write `requirements.txt`**

```
requests
openpyxl
pytest
```

- [ ] **Step 3: Install dependencies**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: all three packages install without error.

- [ ] **Step 4: Commit**

This folder is not part of the `news_agent` git repo (it's a sibling folder, same as `GAODE`). If it has its own git repo, commit there; otherwise skip — GAODE has no git repo either, so this likely doesn't need one. Confirm with `git -C "D:\代码项目\OpenRouterUsage" status` — if it says "not a git repository," there is nothing to commit here, move to Task 2.

---

### Task 2: `parse_chart_data` — parse the API response

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py` (create)
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py` (create)

- [ ] **Step 1: Write the failing test**

Create `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`:

```python
from openrouter_usage import parse_chart_data


def test_parse_chart_data_computes_total_and_sorts():
    payload = {
        "data": {
            "data": [
                {"x": "2025-07-07", "ys": {"model-a": 100, "Others": 50}},
                {"x": "2025-06-30", "ys": {"model-a": 80, "model-b": 20, "Others": 10}},
            ]
        }
    }
    records = parse_chart_data(payload)
    assert [r["date"] for r in records] == ["2025-06-30", "2025-07-07"]
    assert records[0]["total"] == 110
    assert records[1]["total"] == 150
    assert records[0]["models"] == {"model-a": 80, "model-b": 20, "Others": 10}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'openrouter_usage'` (file doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `D:\代码项目\OpenRouterUsage\openrouter_usage.py`:

```python
# -*- coding: utf-8 -*-
"""Weekly OpenRouter token-usage tracker — fetches the public rankings
chart API, accumulates history in Excel, pushes two panels to the
news_agent dashboard. No browser automation needed (unlike GAODE.py) —
the chart is backed by a plain public JSON endpoint.
"""

import os
import datetime
import traceback

import requests
import openpyxl

os.chdir(os.path.dirname(os.path.abspath(__file__)))

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openrouter_run.log")


def _log(msg):
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def parse_chart_data(payload: dict) -> list[dict]:
    """Parse the OpenRouter rankings API response into per-week records.

    Returns a list of {"date": "YYYY-MM-DD", "models": {model: tokens, ...}, "total": int}
    sorted by date ascending. "total" includes the "Others" bucket.
    """
    entries = payload["data"]["data"]
    records = []
    for entry in entries:
        models = entry["ys"]
        records.append({
            "date": entry["x"],
            "models": models,
            "total": sum(models.values()),
        })
    records.sort(key=lambda r: r["date"])
    return records
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (1 passed)

---

### Task 3: `compute_deltas` — week-over-week math

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_openrouter_usage.py`:

```python
from openrouter_usage import compute_deltas


def test_compute_deltas_first_row_has_no_delta():
    rows = compute_deltas([{"date": "2025-06-30", "total": 100}])
    assert rows[0]["wow_delta"] is None
    assert rows[0]["wow_delta_of_delta"] is None


def test_compute_deltas_second_row_has_delta_but_no_delta_of_delta():
    rows = compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ])
    assert rows[1]["wow_delta"] == 50
    assert rows[1]["wow_delta_of_delta"] is None


def test_compute_deltas_third_row_has_both():
    rows = compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
        {"date": "2025-07-14", "total": 130},
    ])
    assert rows[2]["wow_delta"] == -20
    assert rows[2]["wow_delta_of_delta"] == -20 - 50  # -70
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ImportError: cannot import name 'compute_deltas'`

- [ ] **Step 3: Write minimal implementation**

Append to `openrouter_usage.py` (after `parse_chart_data`):

```python
def compute_deltas(records: list[dict]) -> list[dict]:
    """Given records sorted by date ascending (each with 'date' and 'total'),
    return rows annotated with wow_delta and wow_delta_of_delta.

    First row: both None (no prior week to diff against).
    Second row: wow_delta set, wow_delta_of_delta still None (no prior delta).
    """
    rows = []
    prev_total = None
    prev_delta = None
    for r in records:
        total = r["total"]
        delta = None if prev_total is None else total - prev_total
        delta_of_delta = None if (delta is None or prev_delta is None) else delta - prev_delta
        rows.append({
            "date": r["date"],
            "total": total,
            "wow_delta": delta,
            "wow_delta_of_delta": delta_of_delta,
        })
        prev_total = total
        prev_delta = delta
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (4 passed)

---

### Task 4: `build_panels` — dashboard JSON payload

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_openrouter_usage.py`:

```python
from openrouter_usage import build_panels


def test_build_panels_total_panel_structure():
    rows = compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ])
    panels = build_panels(rows)
    total_panel = panels[0]
    assert total_panel["type"] == "line"
    assert total_panel["x_type"] == "date"
    assert total_panel["span_gaps"] is True
    assert total_panel["datasets"] == [
        {"label": "Total Tokens", "data": [
            {"x": "2025-06-30", "y": 100},
            {"x": "2025-07-07", "y": 150},
        ]}
    ]


def test_build_panels_delta_panel_has_two_datasets_with_nulls():
    rows = compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ])
    panels = build_panels(rows)
    delta_panel = panels[1]
    assert delta_panel["type"] == "line"
    assert delta_panel["x_type"] == "date"
    assert delta_panel["span_gaps"] is False
    assert len(delta_panel["datasets"]) == 2
    wow_delta_data = delta_panel["datasets"][0]["data"]
    assert wow_delta_data[0]["y"] is None   # first week: no prior week
    assert wow_delta_data[1]["y"] == 50
    wow_delta_of_delta_data = delta_panel["datasets"][1]["data"]
    assert wow_delta_of_delta_data[0]["y"] is None
    assert wow_delta_of_delta_data[1]["y"] is None  # second week: no prior delta yet
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ImportError: cannot import name 'build_panels'`

- [ ] **Step 3: Write minimal implementation**

Append to `openrouter_usage.py`:

```python
def build_panels(rows: list[dict]) -> list[dict]:
    """Build the two dashboard panels from delta-annotated weekly rows.

    Panel 1: total token usage (single line) — the literal "total instead
    of OpenRouter's per-model stacked chart" the dashboard shows.
    Panel 2: week-over-week change — two lines, span_gaps=False so the
    chart doesn't draw a misleading line across the first 1-2 weeks
    where there's no prior data to diff against.
    """
    total_panel = {
        "type": "line",
        "title": "OpenRouter 周度 Token 总用量",
        "x_type": "date",
        "span_gaps": True,
        "datasets": [
            {
                "label": "Total Tokens",
                "data": [{"x": r["date"], "y": r["total"]} for r in rows],
            }
        ],
    }
    delta_panel = {
        "type": "line",
        "title": "Token 用量周环比变化",
        "x_type": "date",
        "span_gaps": False,
        "datasets": [
            {
                "label": "周环比变化 (WoW Δ)",
                "data": [{"x": r["date"], "y": r["wow_delta"]} for r in rows],
            },
            {
                "label": "环比变化的环比变化 (WoW Δ-of-Δ)",
                "data": [{"x": r["date"], "y": r["wow_delta_of_delta"]} for r in rows],
            },
        ],
    }
    return [total_panel, delta_panel]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (6 passed)

- [ ] **Step 5: Commit (if this folder has its own git repo; otherwise skip — see Task 1 Step 4)**

---

### Task 5: Excel upsert — `Weekly Total` sheet

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`

This sheet uses a **merge-then-rebuild** strategy: read any existing rows into a dict keyed by date, overlay the new rows on top (overwriting matching dates, preserving dates not present in the new fetch), then rewrite the whole sheet sorted by date. This avoids fragile row-index bookkeeping when mixing overwrites and inserts.

- [ ] **Step 1: Write the failing tests**

Append to `test_openrouter_usage.py`:

```python
import openpyxl
from openrouter_usage import upsert_weekly_total_sheet


def _read_weekly_total_rows(wb):
    ws = wb["Weekly Total"]
    return [
        [ws.cell(row=r, column=c).value for c in range(1, 5)]
        for r in range(2, ws.max_row + 1)
    ]


def test_upsert_weekly_total_sheet_inserts_new_rows():
    wb = openpyxl.Workbook()
    rows = compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ])
    upsert_weekly_total_sheet(wb, rows)
    data = _read_weekly_total_rows(wb)
    assert data == [
        ["2025-06-30", 100, None, None],
        ["2025-07-07", 150, 50, None],
    ]


def test_upsert_weekly_total_sheet_overwrites_existing_date_and_preserves_others():
    wb = openpyxl.Workbook()
    upsert_weekly_total_sheet(wb, compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ]))
    # Second run: API revises 2025-07-07's total, adds a new week.
    # 2025-06-30 is no longer in the API's window but must be preserved.
    upsert_weekly_total_sheet(wb, compute_deltas([
        {"date": "2025-07-07", "total": 160},
        {"date": "2025-07-14", "total": 200},
    ]))
    data = _read_weekly_total_rows(wb)
    assert data == [
        ["2025-06-30", 100, None, None],   # preserved, untouched
        ["2025-07-07", 160, None, None],   # overwritten with revised total
        ["2025-07-14", 200, 40, None],     # newly added
    ]
```

Note: the second call's `compute_deltas` only sees the two rows passed to it, so `2025-07-07`'s `wow_delta` is recomputed as `None` (no prior row in *that* call) — this is expected because `upsert_weekly_total_sheet` itself does not recompute deltas; that's `main()`'s job via `_accumulated_totals` (Task 6). This test only verifies the upsert/merge mechanics in isolation.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ImportError: cannot import name 'upsert_weekly_total_sheet'`

- [ ] **Step 3: Write minimal implementation**

Append to `openrouter_usage.py`:

```python
_WEEKLY_TOTAL_HEADERS = ["Date", "Total Tokens", "WoW Δ", "WoW Δ-of-Δ"]


def upsert_weekly_total_sheet(wb, rows: list[dict]) -> None:
    """Upsert rows into the 'Weekly Total' sheet, keyed by Date.

    Dates not present in `rows` (e.g. weeks that rolled out of the API's
    rolling window) are preserved untouched — this is what gives the
    Excel file permanent history beyond what OpenRouter currently exposes.
    """
    if "Weekly Total" not in wb.sheetnames:
        ws = wb.create_sheet("Weekly Total")
    else:
        ws = wb["Weekly Total"]

    existing: dict[str, list] = {}
    for row_idx in range(2, ws.max_row + 1):
        vals = [ws.cell(row=row_idx, column=c).value for c in range(1, 5)]
        if vals[0]:
            existing[str(vals[0])] = vals

    for r in rows:
        existing[r["date"]] = [r["date"], r["total"], r["wow_delta"], r["wow_delta_of_delta"]]

    if ws.max_row > 0:
        ws.delete_rows(1, ws.max_row)
    ws.append(_WEEKLY_TOTAL_HEADERS)
    for date_str in sorted(existing.keys()):
        ws.append(existing[date_str])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (8 passed)

---

### Task 6: Excel upsert — `Per-Model Detail` sheet

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`

This sheet uses the same merge-then-rebuild strategy, but keyed by date at the *block* level (a whole week's worth of model rows is replaced together), since the set of models per week can grow or shrink.

- [ ] **Step 1: Write the failing tests**

Append to `test_openrouter_usage.py`:

```python
from openrouter_usage import upsert_model_detail_sheet


def _read_model_detail_rows(wb):
    ws = wb["Per-Model Detail"]
    return [
        [ws.cell(row=r, column=c).value for c in range(1, 4)]
        for r in range(2, ws.max_row + 1)
    ]


def test_upsert_model_detail_sheet_inserts_and_refreshes_by_date():
    wb = openpyxl.Workbook()
    upsert_model_detail_sheet(wb, [
        {"date": "2025-06-30", "models": {"model-a": 80, "model-b": 20}, "total": 100},
    ])
    assert _read_model_detail_rows(wb) == [
        ["2025-06-30", "model-a", 80],
        ["2025-06-30", "model-b", 20],
    ]

    # Second run: 2025-06-30's model breakdown is refreshed (model-b dropped,
    # model-c appears); a new week is added. Both should be reflected exactly,
    # nothing duplicated.
    upsert_model_detail_sheet(wb, [
        {"date": "2025-06-30", "models": {"model-a": 90, "model-c": 5}, "total": 95},
        {"date": "2025-07-07", "models": {"model-a": 100}, "total": 100},
    ])
    assert _read_model_detail_rows(wb) == [
        ["2025-06-30", "model-a", 90],
        ["2025-06-30", "model-c", 5],
        ["2025-07-07", "model-a", 100],
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ImportError: cannot import name 'upsert_model_detail_sheet'`

- [ ] **Step 3: Write minimal implementation**

Append to `openrouter_usage.py`:

```python
_MODEL_DETAIL_HEADERS = ["Date", "Model", "Tokens"]


def upsert_model_detail_sheet(wb, records: list[dict]) -> None:
    """Upsert per-model rows into the 'Per-Model Detail' sheet, keyed by Date.

    All existing rows for a date present in `records` are replaced with the
    fresh set (handles models appearing/disappearing within a week). Dates
    not present in `records` are left untouched.
    """
    if "Per-Model Detail" not in wb.sheetnames:
        ws = wb.create_sheet("Per-Model Detail")
    else:
        ws = wb["Per-Model Detail"]

    incoming_dates = {r["date"] for r in records}

    kept_rows = []
    for row_idx in range(2, ws.max_row + 1):
        date_val = ws.cell(row=row_idx, column=1).value
        if date_val is None:
            continue
        if str(date_val) not in incoming_dates:
            kept_rows.append([
                ws.cell(row=row_idx, column=1).value,
                ws.cell(row=row_idx, column=2).value,
                ws.cell(row=row_idx, column=3).value,
            ])

    new_rows = [
        [r["date"], model, tokens]
        for r in records
        for model, tokens in r["models"].items()
    ]
    all_rows = sorted(kept_rows + new_rows, key=lambda row: row[0])

    if ws.max_row > 0:
        ws.delete_rows(1, ws.max_row)
    ws.append(_MODEL_DETAIL_HEADERS)
    for row in all_rows:
        ws.append(row)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (9 passed)

- [ ] **Step 5: Commit (if this folder has its own git repo; otherwise skip)**

---

### Task 7: `_accumulated_totals` — merge full history for delta recompute

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`
- Modify: `D:\代码项目\OpenRouterUsage\test_openrouter_usage.py`

This closes the gap noted in the spec: if OpenRouter retroactively corrects last week's total, `compute_deltas` must re-run over the *full* accumulated history (including weeks already saved from prior runs, not just this run's fetch), otherwise stored deltas would go stale.

- [ ] **Step 1: Write the failing test**

Append to `test_openrouter_usage.py`:

```python
from openrouter_usage import _accumulated_totals


def test_accumulated_totals_merges_existing_and_new():
    wb = openpyxl.Workbook()
    upsert_weekly_total_sheet(wb, compute_deltas([
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
    ]))
    # This run's fetch only returned the newest 2 weeks (older one rolled
    # out of the API's window) — but 2025-06-30 must still be included.
    new_records = [
        {"date": "2025-07-07", "total": 150},
        {"date": "2025-07-14", "total": 200},
    ]
    merged = _accumulated_totals(wb, new_records)
    assert merged == [
        {"date": "2025-06-30", "total": 100},
        {"date": "2025-07-07", "total": 150},
        {"date": "2025-07-14", "total": 200},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: FAIL with `ImportError: cannot import name '_accumulated_totals'`

- [ ] **Step 3: Write minimal implementation**

Append to `openrouter_usage.py`:

```python
def _accumulated_totals(wb, records: list[dict]) -> list[dict]:
    """Merge this run's per-week totals with totals already stored in the
    Weekly Total sheet, so delta recompute covers full accumulated history
    (not just the weeks returned by this run's API call).
    """
    totals: dict[str, int] = {}
    if "Weekly Total" in wb.sheetnames:
        ws = wb["Weekly Total"]
        for row_idx in range(2, ws.max_row + 1):
            date_val = ws.cell(row=row_idx, column=1).value
            total_val = ws.cell(row=row_idx, column=2).value
            if date_val is not None and total_val is not None:
                totals[str(date_val)] = total_val
    for r in records:
        totals[r["date"]] = r["total"]
    return [{"date": d, "total": totals[d]} for d in sorted(totals.keys())]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (10 passed)

---

### Task 8: Fetch, push, and `main()` — wiring it together

**Files:**
- Modify: `D:\代码项目\OpenRouterUsage\openrouter_usage.py`

These functions touch the network (OpenRouter API, news_agent dashboard) and are not unit tested — consistent with GAODE.py's precedent, where fetch/push are thin single-responsibility wrappers and the risk is in the logic functions already tested in Tasks 2–7, not in the HTTP calls themselves.

- [ ] **Step 1: Add fetch, push, and main()**

Append to `openrouter_usage.py`:

```python
_API_BASE = "http://47.239.66.248"
_API_KEY = "b1445fd803c77c5bff4b0eeced29f5b84c752d0bbd6642f89bd44c732a1646fa"
_SCRIPT_NAME = "openrouter_usage"


def fetch_chart_data() -> dict:
    resp = requests.get(
        "https://openrouter.ai/api/frontend/v1/rankings/model-rankings-chart",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def push_to_dashboard(rows: list[dict], excel_path: str) -> None:
    panels = build_panels(rows)
    sess = requests.Session()
    sess.trust_env = False  # don't pick up Windows proxy settings (see GAODE.py)
    sess.post(
        f"{_API_BASE}/api/report",
        headers={"X-API-Key": _API_KEY},
        json={
            "script": _SCRIPT_NAME,
            "status": "ok",
            "expected_interval_hours": 168,  # 7 days
            "panels": panels,
        },
        timeout=30,
    ).raise_for_status()
    with open(excel_path, "rb") as f:
        sess.post(
            f"{_API_BASE}/api/report/{_SCRIPT_NAME}/excel",
            headers={"X-API-Key": _API_KEY},
            files={"file": ("openrouter_usage.xlsx", f)},
            timeout=60,
        ).raise_for_status()


def main():
    _log("=" * 60)
    _log("Script started")

    try:
        payload = fetch_chart_data()
    except Exception:
        _log("Fetch FAILED:\n" + traceback.format_exc())
        raise

    records = parse_chart_data(payload)
    _log(f"Fetched {len(records)} weeks from API")

    excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openrouter_usage.xlsx")
    wb = openpyxl.load_workbook(excel_path) if os.path.exists(excel_path) else openpyxl.Workbook()

    upsert_model_detail_sheet(wb, records)

    full_history = _accumulated_totals(wb, records)
    rows = compute_deltas(full_history)
    upsert_weekly_total_sheet(wb, rows)

    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]  # drop openpyxl's default blank sheet

    wb.save(excel_path)
    _log(f"Excel saved: {excel_path}")

    try:
        push_to_dashboard(rows, excel_path)
        _log("Dashboard push OK")
    except Exception:
        _log(f"Dashboard push FAILED:\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite once more to confirm nothing broke**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe -m pytest test_openrouter_usage.py -v
```

Expected: PASS (10 passed) — Task 8 added no new tests, just wiring.

- [ ] **Step 3: Manual end-to-end run**

```bash
cd "D:\代码项目\OpenRouterUsage"
venv\Scripts\python.exe openrouter_usage.py
```

Expected: script exits with no traceback. Check:
1. `openrouter_run.log` contains `Script started` → `Fetched N weeks from API` → `Excel saved` → `Dashboard push OK`
2. `openrouter_usage.xlsx` exists with `Weekly Total` and `Per-Model Detail` sheets populated
3. Run it a second time immediately — log should show the same weeks fetched again, Excel should not duplicate any rows (upsert, not append)

- [ ] **Step 4: Commit (if this folder has its own git repo; otherwise skip)**

---

### Task 9: Fix dashboard.html — proper time axis for `x_type: "date"` panels

**Files:**
- Modify: `D:\代码项目\news_agent\templates\dashboard.html:574-577`

**Why this is needed:** `renderScriptCharts()` has two branches — `isDoy` (used by GAODE, `x_type: "day_of_year"`) and the fallback branch for everything else, including `x_type: "date"`. The fallback branch's x-axis has no `type` set at all, so Chart.js defaults to a category scale instead of a real time scale. This has never been caught before because GAODE is the only script panel that exists today and it always uses `day_of_year`. The chartjs-adapter-date-fns library is already loaded in this template (see the `<script>` tag near the top of the `{% block scripts %}` section), so fixing this is a small, scoped change.

- [ ] **Step 1: Make the edit**

In `D:\代码项目\news_agent\templates\dashboard.html`, find this block (around line 574, the `else`-branch of the `scales: isDoy ? {...} : {...}` ternary inside `renderScriptCharts()`):

```js
          } : {
            x: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
          },
```

Replace it with:

```js
          } : {
            x: {
              type: 'time',
              time: { tooltipFormat: 'yyyy-MM-dd', displayFormats: { day: 'MM/dd', week: 'MM/dd' } },
              grid: { color: 'rgba(0,0,0,0.04)' },
              ticks: { font: { size: 11 } },
            },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
          },
```

No other change is needed in this function: the existing tooltip `title` callback already does `if (!isDoy) return items[0].label;`, and Chart.js auto-populates `items[0].label` from `time.tooltipFormat` once the x-axis is a real time scale — so the date will render correctly without touching the tooltip callback.

- [ ] **Step 2: Manual verification (no automated frontend tests exist in this codebase for dashboard.html — verify by running the app)**

```bash
cd "D:\代码项目\news_agent"
.venv\Scripts\python.exe app.py
```

Visit `http://localhost:5000/dashboard` in a browser (once Task 10's data exists, or temporarily push a test panel via `curl` using the `/api/report` contract from `docs/dashboard-integration-guide.md`). Confirm:
1. The x-axis shows actual calendar dates spaced proportionally to time (not evenly-spaced categories)
2. Hovering shows a tooltip with the correct `yyyy-MM-dd` date

- [ ] **Step 3: Commit**

```bash
cd "D:\代码项目\news_agent"
git add templates/dashboard.html
git commit -m "$(cat <<'EOF'
Fix x_type=date chart panels to use a real Chart.js time axis

renderScriptCharts()'s non-day_of_year branch never set scales.x.type,
so Chart.js defaulted to a category scale instead of a time scale.
Never caught before because GAODE (the only script panel so far) uses
x_type=day_of_year. Needed for the upcoming openrouter_usage panels,
which use x_type=date.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

---

### Task 10: Update the dashboard integration guide

**Files:**
- Modify: `D:\代码项目\news_agent\docs\dashboard-integration-guide.md`

- [ ] **Step 1: Add a reference-table row**

Find this table in the guide:

```markdown
## Reference — Existing Scripts

| Script | `script` name | Panel type | `x_type` | Excel upload |
|---|---|---|---|---|
| GAODE.py | `gaode` | line | `day_of_year` | ✓ |
```

Replace with:

```markdown
## Reference — Existing Scripts

| Script | `script` name | Panel type | `x_type` | Excel upload |
|---|---|---|---|---|
| GAODE.py | `gaode` | line | `day_of_year` | ✓ |
| openrouter_usage.py | `openrouter_usage` | line (2 panels) | `date` | ✓ |
```

- [ ] **Step 2: Commit**

```bash
cd "D:\代码项目\news_agent"
git add docs/dashboard-integration-guide.md
git commit -m "$(cat <<'EOF'
Document openrouter_usage in the dashboard integration guide

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

---

### Task 11: Deploy and schedule (manual — for the user, not the agent)

This task has no code; it's the deployment checklist to hand back to the user once Tasks 1–10 are done.

- [ ] **Step 1:** Deploy the dashboard.html fix to the server:
  ```bash
  bash /opt/tofuhouse/news_agent/scripts/deploy.sh
  ```
- [ ] **Step 2:** Run `D:\代码项目\OpenRouterUsage\openrouter_usage.py` manually once to confirm the live push works end-to-end against the deployed server.
- [ ] **Step 3:** Open Windows Task Scheduler, create a new weekly-triggered task pointing at:
  - Program: `D:\代码项目\OpenRouterUsage\venv\Scripts\python.exe`
  - Arguments: `openrouter_usage.py`
  - Start in: `D:\代码项目\OpenRouterUsage`
  (Same shape as the existing GAODE.py task — see Task Scheduler's GAODE entry for reference.)
- [ ] **Step 4:** Visit `/dashboard` and confirm both new panels render under the `openrouter_usage` section, with the visibility toggle (🔓/🔒) working for admin.
