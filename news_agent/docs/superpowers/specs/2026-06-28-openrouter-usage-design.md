# OpenRouter Weekly Usage Dashboard — Design Spec
_Date: 2026-06-28_

---

## Overview

Add a new standalone data script that tracks weekly LLM token usage across OpenRouter, following the existing GAODE.py integration pattern (see `docs/dashboard-integration-guide.md`). Unlike GAODE, this script needs **no browser automation** — the chart on https://openrouter.ai/rankings is backed by a fully public JSON API.

Two dashboard panels are produced:
1. **Total Token Usage** — single line, weekly total tokens across all models (the user explicitly wants the *total*, not OpenRouter's per-model stacked view)
2. **Week-over-Week Change** — two lines: the week-over-week delta in total tokens, and the week-over-week change of that delta (second-order difference)

---

## Data Source

```
GET https://openrouter.ai/api/frontend/v1/rankings/model-rankings-chart
```

- No auth, no cookies required (`access-control-allow-origin: *`, CDN-cached with `cache-control: public, max-age=300`)
- Verified via direct `curl` with only a `User-Agent` header — works
- Returns **full history on every call** (currently 52 weeks, ~1 year, rolling window — older weeks silently drop off over time as new weeks are added)

### Response shape

```json
{
  "data": {
    "data": [
      {
        "x": "2025-06-30",
        "ys": {
          "anthropic/claude-4-sonnet-20250522": 323846517367,
          "google/gemini-2.0-flash-001": 237174711398,
          "...": "...",
          "Others": 714630073890
        }
      },
      { "x": "2025-07-07", "ys": { "...": "..." } }
    ]
  }
}
```

- `x`: week start date (`YYYY-MM-DD`)
- `ys`: token counts per model for that week. **The set of top models changes from week to week** (confirmed — only ~9 named models + an `"Others"` aggregate bucket are present per entry; the named models rotate as usage shifts).
- Weekly total = `sum(ys.values())` (including `"Others"`)

Because the API's window rolls forward and could drop old weeks, the script **must accumulate history locally** rather than overwrite — confirmed with user (deviates from the simpler "just overwrite" default, intentionally, to preserve a permanent record beyond OpenRouter's rolling window).

---

## Script — `D:\代码项目\OpenRouterUsage\openrouter_usage.py`

Mirrors the GAODE.py pattern minus Selenium:
- Own folder, own lightweight venv (`requests` + `openpyxl` only — no Selenium/ChromeDriver, sidestepping that entire class of bugs)
- `os.chdir()` to script directory on startup (Task Scheduler cwd safety, same as GAODE)
- File-based logging to `openrouter_run.log` in the same folder — same format as `gaode_run.log` (timestamped lines, full traceback on failure), so Task Scheduler failures stay diagnosable without console output
- Triggered weekly via Windows Task Scheduler — the script does not self-schedule; the user configures the trigger cadence/time in Task Scheduler, same as GAODE

### Logic

1. Fetch the JSON endpoint (`requests.get`, no special headers needed beyond default `User-Agent`)
2. Parse `data.data[]` into `(date, model, tokens)` tuples; compute `total[date] = sum(ys.values())` per week
3. **Upsert into Excel by date**:
   - If a row for that date already exists, overwrite it (picks up any retroactive corrections OpenRouter makes to recent weeks)
   - If the date is new, insert it
   - Never delete existing rows, even if a date later falls outside the API's current rolling window — this is what preserves history permanently
4. Recompute `WoW Δ` and `WoW Δ-of-Δ` for the full accumulated date range (not just the newly-fetched weeks) after the upsert, since a correction to last week's total would otherwise leave the delta columns stale
5. Build the two dashboard panels (below) from the full accumulated history (not just what the API returned this run)
6. Push to dashboard via the existing `/api/report` and `/api/report/openrouter_usage/excel` endpoints (same auth/contract as GAODE — see `docs/dashboard-integration-guide.md`)
7. Any failure (network, parse, push) is logged to `openrouter_run.log` with full traceback; the script does not silently swallow errors

---

## Excel — `openrouter_usage.xlsx`, two sheets

### `Weekly Total`
| Date | Total Tokens | WoW Δ | WoW Δ-of-Δ |
|---|---|---|---|
| 2025-06-30 | 2061245... | _(blank — no prior week)_ | _(blank)_ |
| 2025-07-07 | 2271521... | +210276... | _(blank — no prior Δ)_ |
| 2025-07-14 | 2200651... | -70870... | -281146... |

- `WoW Δ[t] = Total[t] - Total[t-1]`
- `WoW Δ-of-Δ[t] = WoW Δ[t] - WoW Δ[t-1]`
- First row: both blank (no prior week to diff against)
- Second row: `WoW Δ` populated, `WoW Δ-of-Δ` still blank (no prior delta to diff against)
- This sheet is also the direct source for both dashboard panels

### `Per-Model Detail`
| Date | Model | Tokens |
|---|---|---|
| 2025-06-30 | anthropic/claude-4-sonnet-20250522 | 323846517367 |
| 2025-06-30 | google/gemini-2.0-flash-001 | 237174711398 |
| ... | ... | ... |

- Long/tidy format, not wide — the top-model set changes weekly, so a wide (one-column-per-model) layout would accumulate 100+ sparse columns over a year. Long format avoids that and is simple to append/upsert (delete-then-reinsert all rows for a given date, same upsert-by-date rule as the Weekly Total sheet).
- Not charted on the dashboard (per user's request — dashboard shows the *total*, not the per-model stack) but downloadable via the same Excel button pattern as GAODE's panel.

---

## Dashboard — two panels under the `openrouter_usage` script section

Both use the existing generic panel schema from `docs/dashboard-integration-guide.md` (`type: "line"`, `x_type: "date"`) — no new dashboard/template code needed beyond what GAODE already exercises, **except**:

- The line-chart renderer must tolerate `null`/missing `y` values without bridging them with a connecting line (`span_gaps: false` for the WoW panel — a gap means "no prior data to diff," not "missing measurement," and drawing a line across it would be misleading). This needs verifying against current `renderScriptCharts()` behavior in `dashboard.html` for non-`day_of_year` (i.e. `x_type: "date"`) panels — today's code path already accepts `span_gaps` from the panel payload but this is the first panel to actually require `false` there, so it should be exercised explicitly in implementation/testing rather than assumed to already work.

### Panel A — "OpenRouter 周度 Token 总用量"
- `type: "line"`, `x_type: "date"`, `span_gaps: true`
- Single dataset: `Total Tokens` over time

### Panel B — "Token 用量周环比变化"
- `type: "line"`, `x_type: "date"`, `span_gaps: false`
- Two datasets: `周环比变化 (WoW Δ)` and `环比变化的环比变化 (WoW Δ-of-Δ)`

### Status badge
- `script: "openrouter_usage"`
- `expected_interval_hours: 168` (7 days)

---

## Error Handling

- Network/API failure on fetch: log full traceback to `openrouter_run.log`, exit without touching the existing Excel file or pushing to the dashboard (stale-but-correct data beats no data)
- Push failure (dashboard unreachable): same non-fatal try/except pattern as GAODE — log and continue; Excel file is still updated locally even if the push fails, so the local Excel can be uploaded later via a one-off script in the same vein as `upload_to_dashboard.py` if needed
- Malformed/unexpected API response shape: treat as a hard failure (log + raise) rather than guessing — this endpoint is undocumented and could change shape without notice

---

## Out of Scope

- Per-model dashboard charts (Excel has the data; not charted per explicit user request)
- Historical backfill beyond what the API currently returns on first run (52 weeks as of design time) — anything OpenRouter has already rolled out of its window before this script's first run is unrecoverable
- Automatic Task Scheduler registration — user configures the weekly trigger manually, same as GAODE
