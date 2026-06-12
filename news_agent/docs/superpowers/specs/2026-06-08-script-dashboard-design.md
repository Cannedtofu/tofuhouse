# Script Monitoring Dashboard — Design Spec
_Date: 2026-06-08_

---

## Overview

Extend the existing `数据` dashboard tab to show:
1. **Status monitoring** — a live health table for 5–6 locally-running data scripts
2. **Data panels** — chart/table visualisations pushed by each script after each run
3. **Excel download** — per-script download of the most recently uploaded Excel file
4. **Panel customisation** — user can show/hide individual script panels via a gear modal

Scripts run on the user's local machine, parse their own output, and push chart-ready JSON to the server. The server stores and serves; it does no data parsing.

---

## Layout (Layout C — approved)

```
数据 tab
│
├── Status strip (always visible)
│   [ 🟢 gaode · 1h ago ]  [ 🟢 script-b · 3h ago ]  [ 🔴 script-c · 8h ago ]   ⚙ 自定义
│
├── 脚本状态详情  (collapsible, closed by default)
│   ┌──────────────────────────────────────────────────────────┐
│   │ 脚本        上次成功        预期间隔     状态             │
│   │ gaode       2026-06-08      24h          🟢               │
│   │ script-c    2026-06-07      24h          🔴 overdue       │
│   └──────────────────────────────────────────────────────────┘
│
├── GPU 算力价格指数  (existing card — unchanged)
│
├── Script panels (shown/hidden per localStorage prefs)
│   ├── 高德交通监测
│   │   └── 路网高延时运行时间占比 — 年度对比 (line chart)
│   │       ⬇ 下载最新 Excel
│   ├── Script B panel
│   │   └── [chart or table per pushed panels payload]
│   └── ...
│
└── ⚙ 自定义 modal
    ☑ 高德交通监测
    ☑ Script B
    ☐ Script C
    [ 保存 ]
```

**Status colour rules:**
- 🟢 green — last push was `status: ok` AND `now - pushed_at ≤ expected_interval_hours`
- 🔴 red — last push was `status: error` OR overdue (no push within expected interval)
- Grey — script has never pushed (new / not yet registered)

---

## Data Model

Two new SQLite tables added via idempotent `ALTER TABLE` migrations in `db/core.py`.

### `script_reports`
```sql
CREATE TABLE IF NOT EXISTS script_reports (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    script_name             TEXT    NOT NULL,
    status                  TEXT    NOT NULL CHECK(status IN ('ok','error')),
    error_message           TEXT,
    data_json               TEXT,           -- full panels payload as JSON string
    pushed_at               TEXT    NOT NULL,
    expected_interval_hours REAL    NOT NULL DEFAULT 24
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_script_reports_name
    ON script_reports(script_name);         -- one row per script, replaced on each push
```

One row per script name. Each push upserts (INSERT OR REPLACE) so the table always holds only the latest report per script.

### `script_files`
```sql
CREATE TABLE IF NOT EXISTS script_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    script_name TEXT    NOT NULL UNIQUE,    -- one row per script
    filename    TEXT    NOT NULL,
    file_data   BLOB    NOT NULL,
    uploaded_at TEXT    NOT NULL
);
```

One row per script name. Upload replaces the existing row.

Both tables live in the existing `news.db`. DB operations go in a new `db/script_reports.py` module following the existing layer pattern.

---

## API Endpoints

### Authentication
All `/api/report*` routes require header `X-API-Key: <value>`. The key is stored in `.env` as `REPORT_API_KEY` and loaded via `config.py`. Missing or wrong key → `403`. No user session required — scripts call this directly.

### `POST /api/report`
Push a status ping and optional chart/table panels.

**Request body (JSON):**
```json
{
  "script": "gaode",
  "status": "ok",
  "error": "optional error message",
  "expected_interval_hours": 24,
  "panels": [
    {
      "type": "line",
      "title": "路网高延时运行时间占比 — 年度对比",
      "x_type": "day_of_year",
      "span_gaps": true,
      "datasets": [
        { "label": "2023", "data": [{"x": "07-06", "y": 17.38}, ...] },
        { "label": "2024", "data": [{"x": "01-01", "y": 16.5},  ...] }
      ]
    }
  ]
}
```

`panels` is optional — a ping with only `script`/`status`/`expected_interval_hours` is valid.

**Panel types:**
| type | required fields |
|------|----------------|
| `line` | `title`, `datasets` (label + data array of `{x, y}`), optional `x_type`, `span_gaps` |
| `bar`  | same as line |
| `table` | `title`, `headers` (string[]), `rows` (string[][]) |

**Response:** `{"ok": true}` or `{"error": "..."}` + appropriate status code.

**Server behaviour:** upserts into `script_reports` (INSERT OR REPLACE on `script_name`).

### `POST /api/report/<script>/excel`
Upload the most recent Excel file for a script. Multipart form-data, field name `file`.

**Server behaviour:** upserts into `script_files` (INSERT OR REPLACE on `script_name`). Old file data is discarded — only the most recent is kept.

**Response:** `{"ok": true}`.

### `GET /api/report/<script>/excel`
Download the stored Excel file. Returns the file with `Content-Disposition: attachment`. Returns `404` if no file has been uploaded for that script. Requires user login (normal Flask `@login_required`), not API key.

---

## Dashboard Implementation

### Backend (`app.py`)

`GET /dashboard` loads:
- `script_reports` — all rows (one per script)
- Derives status strip data: name, pushed_at, status, expected_interval_hours, is_overdue
- Passes to `dashboard.html` as `script_panels` template variable

No new polling — dashboard data is rendered server-side on page load. The page auto-refreshes the status strip every 60 seconds via a lightweight `fetch('/dashboard/status')` call.

New route: `GET /dashboard/status` returns JSON of all script statuses (name, pushed_at, status, overdue) — used for the 60-second auto-refresh without full page reload.

### Frontend (`dashboard.html`)

**Status strip** — rendered from `script_panels`. Each badge is green or red based on `is_overdue` and `status`. Clicking a badge scrolls to the corresponding panel.

**Collapsible status table** — Bootstrap collapse, closed by default. Shows full detail: script name, last success time, expected interval, status indicator.

**⚙ 自定义 modal** — Bootstrap modal. One checkbox per script panel. On save, writes visibility map to `localStorage` as `dashboardPanels` JSON key. On page load, JS reads this key and hides unchecked panels. GPU panel has no checkbox (always visible).

**Script panels** — one card per script in `script_reports`. If `data_json` is null (status-only push with no `panels`), the card shows the script name, last-push time, and status badge only — no chart. If `data_json` contains panels:
- `line` / `bar` → Chart.js 4.4.3 (already loaded). `span_gaps: true` when `panel.span_gaps` is set. `x_type: "day_of_year"` renders a categorical x-axis from `01-01` to `12-31` with month labels (Jan … Dec); data points use `MM-DD` string keys.
- `table` → plain Bootstrap `<table>` with `headers` as `<th>` and `rows` as `<tr>`.
- Excel download button links to `GET /api/report/<script>/excel` — shown only if a file exists for that script.

Each panel card shows the script name as heading and `pushed_at` as a subtitle.

---

## GaoDe Script Integration

Add ~30 lines to the end of `GAODE.py` (after `book.save()`):

```python
import requests, openpyxl as _xl
from datetime import datetime as _dt

_API_BASE = "https://47.239.66.248"
_API_KEY  = "..."   # from environment or hardcoded

# 1. Parse Excel into annual-comparison datasets for 路网高延时运行时间占比
_wb = _xl.load_workbook(file_path, data_only=True)
_ws = _wb.active
_datasets: dict[str, list] = {}
for _row in _ws.iter_rows(min_row=2, values_only=True):
    if not _row[0]: continue
    _d   = _dt.fromisoformat(str(_row[0])[:19])
    _yr  = str(_d.year)
    _val = float(str(_row[2]).rstrip('%'))
    _datasets.setdefault(_yr, []).append({"x": _d.strftime("%m-%d"), "y": _val})

_panels = [{
    "type":     "line",
    "title":    "路网高延时运行时间占比 — 年度对比",
    "x_type":   "day_of_year",
    "span_gaps": True,
    "datasets": [{"label": yr, "data": pts}
                 for yr, pts in sorted(_datasets.items())]
}]

_ok = float(data[0][3].rstrip('%')) > 0
_sess = requests.Session()
_sess.verify = False

# 2. Push status + chart data
_sess.post(f"{_API_BASE}/api/report",
    headers={"X-API-Key": _API_KEY},
    json={"script": "gaode", "status": "ok" if _ok else "error",
          "expected_interval_hours": 24, "panels": _panels})

# 3. Upload Excel for download
with open(file_path, "rb") as _f:
    _sess.post(f"{_API_BASE}/api/report/gaode/excel",
        headers={"X-API-Key": _API_KEY},
        files={"file": ("GaoDe.xlsx", _f)})
```

The same pattern applies to other scripts: parse locally, send chart-ready JSON + optional Excel upload.

---

## File Structure Changes

```
app.py                          add: /api/report, /api/report/<script>/excel,
                                     /dashboard/status routes
config.py                       add: REPORT_API_KEY
db/
  script_reports.py             new: upsert_script_report(), get_all_script_reports(),
                                     upsert_script_file(), get_script_file()
  __init__.py                   re-export new functions
templates/
  dashboard.html                extend: status strip, collapsible table,
                                         gear modal, script panel cards

docs/superpowers/specs/
  2026-06-08-script-dashboard-design.md   this file
```

No new Python dependencies. `openpyxl` already installed in news_agent venv.

---

## Out of Scope

- Per-script chart configuration from the UI (scripts own their panel format)
- Historical report storage (only latest report per script is kept)
- Push notifications / alerts when a script goes red
- User-specific panel ordering (localStorage visibility only)
