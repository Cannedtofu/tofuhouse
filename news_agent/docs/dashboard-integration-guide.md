# Dashboard Integration Guide

This file explains how to connect a new external script to the 数据 Dashboard at `https://47.239.66.248/dashboard`.

---

## How It Works

Scripts push data to the news_agent server via two HTTP endpoints. The server stores the data; the dashboard reads it on page load and renders charts/tables. No server-side parsing — scripts are responsible for sending chart-ready JSON.

---

## Step 1 — Push a Report

**Endpoint:** `POST https://47.239.66.248/api/report`  
**Auth:** `X-API-Key: <key>` header (get the key from `.env` → `REPORT_API_KEY`)  
**Content-Type:** `application/json`

### Request Body

```json
{
  "script": "my_script_name",
  "status": "ok",
  "expected_interval_hours": 24,
  "panels": [ ... ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `script` | string | ✓ | Unique name — appears as the panel heading and status badge. Use lowercase with underscores or spaces, e.g. `"gaode"`, `"gpu prices"` |
| `status` | `"ok"` \| `"error"` | ✓ | `"error"` turns the status badge red |
| `expected_interval_hours` | number | ✓ | How often the script is expected to run. If the last push is older than this, the badge turns red (overdue) |
| `panels` | array | optional | Chart/table definitions — see below. Omit or pass `[]` if the script only needs a status badge |
| `error` | string | optional | Human-readable error message, shown when `status = "error"` |

One push per script run. The server upserts — the latest push always replaces the previous one.

---

## Step 2 — Define Panels

Each item in `panels` is one chart or table card rendered inside the script's panel.

### Line or Bar Chart

```json
{
  "type": "line",
  "title": "My Metric Over Time",
  "x_type": "date",
  "span_gaps": false,
  "datasets": [
    {
      "label": "Series A",
      "data": [
        { "x": "2025-01-01", "y": 42.5 },
        { "x": "2025-01-02", "y": 44.1 }
      ]
    }
  ]
}
```

| Field | Values | Notes |
|---|---|---|
| `type` | `"line"` \| `"bar"` | Chart type |
| `title` | string | Shown above the chart |
| `x_type` | `"date"` \| `"day_of_year"` | See below |
| `span_gaps` | bool | Connect missing data points with a line |
| `datasets` | array | One entry per series/line |
| `datasets[].label` | string | Legend label |
| `datasets[].data` | array of `{x, y}` | x is a string, y is a number |

#### `x_type: "date"` — time series
- `x` values are ISO date strings: `"2025-06-08"` or `"2025-06-08T14:30:00"`
- x-axis renders as a standard time axis

#### `x_type: "day_of_year"` — annual comparison
- `x` values are month-day strings: `"01-15"`, `"06-08"`, `"12-31"`
- Each dataset is one year; the x-axis shows Jan–Dec
- Use `"span_gaps": true` if data has holes
- Example use case: compare the same metric across multiple years on one chart

Multiple datasets = multiple lines on the same chart (one color per dataset, auto-assigned).

### Table

```json
{
  "type": "table",
  "title": "Recent Records",
  "headers": ["Date", "City", "Value"],
  "rows": [
    ["2025-06-08", "Beijing", "12.3%"],
    ["2025-06-07", "Shanghai", "10.1%"]
  ]
}
```

| Field | Notes |
|---|---|
| `headers` | Column header strings |
| `rows` | Array of arrays — each inner array is one row, values are strings |

---

## Step 3 — Upload Excel File (Optional)

If your script produces an Excel file, upload it so users can download it from the dashboard.

**Endpoint:** `POST https://47.239.66.248/api/report/<script_name>/excel`  
**Auth:** same `X-API-Key` header  
**Body:** `multipart/form-data` with field name `file`

Only the most recent upload is kept. The download button appears automatically on the panel.

---

## Step 4 — Python Boilerplate

Copy this block into your script after all main logic runs. Replace the constants and build your own `_panels` list.

```python
# ---------------------------------------------------------------------------
# Push to news_agent dashboard (non-fatal)
# ---------------------------------------------------------------------------
import requests as _req

_API_BASE = "https://47.239.66.248"
_API_KEY  = "b1445fd803c77c5bff4b0eeced29f5b84c752d0bbd6642f89bd44c732a1646fa"  # REPORT_API_KEY from server .env

_panels = [
    {
        "type":      "line",
        "title":     "My Metric",
        "x_type":    "date",          # or "day_of_year" for annual comparison
        "span_gaps": True,
        "datasets": [
            {
                "label": "Series A",
                "data":  [{"x": "2025-06-08", "y": 99.0}],  # build from your data
            }
        ],
    }
]

_sess = _req.Session()
_sess.verify = False  # server uses self-signed cert

try:
    _sess.post(
        f"{_API_BASE}/api/report",
        headers={"X-API-Key": _API_KEY},
        json={
            "script":                   "my_script_name",  # unique identifier
            "status":                   "ok",              # or "error"
            "expected_interval_hours":  24,
            "panels":                   _panels,
            # "error": "message if status is error",
        },
        timeout=30,
    )

    # Optional: upload Excel file
    # with open("output.xlsx", "rb") as _f:
    #     _sess.post(
    #         f"{_API_BASE}/api/report/my_script_name/excel",
    #         headers={"X-API-Key": _API_KEY},
    #         files={"file": ("output.xlsx", _f)},
    #         timeout=60,
    #     )

    print("Dashboard push OK")
except Exception as _e:
    print(f"Dashboard push failed (non-fatal): {_e}")
```

---

## Reference — Existing Scripts

| Script | `script` name | Panel type | `x_type` | Excel upload |
|---|---|---|---|---|
| GAODE.py | `gaode` | line | `day_of_year` | ✓ |

Add a row here each time a new script is integrated.

---

## Server Details

| Item | Value |
|---|---|
| Server | `47.239.66.248` (Alibaba Cloud ECS, Hong Kong) |
| App path | `/opt/tofuhouse/news_agent` |
| API key env var | `REPORT_API_KEY` in `/opt/tofuhouse/news_agent/.env` |
| Dashboard URL | `https://47.239.66.248/dashboard` (login required) |

---

## Notes

- The `script` name is the unique key — pushing with the same name overwrites the previous report. Choose a stable, consistent name.
- All pushes use UTC timestamps internally. Display on the dashboard converts to SGT.
- If `panels` is empty or omitted, the script still appears in the status strip and status table — useful for scripts that just need health monitoring with no data visualization.
- The SSL warning from `verify=False` is suppressed by default in most environments. To suppress explicitly: `import urllib3; urllib3.disable_warnings()`.
