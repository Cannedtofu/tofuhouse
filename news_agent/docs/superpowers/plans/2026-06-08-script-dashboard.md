# Script Monitoring Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the 数据 dashboard tab with a script status strip, per-script data panels rendered from locally-parsed JSON pushed by external scripts, and Excel file download.

**Architecture:** Scripts push chart-ready JSON + optional Excel blobs to new API endpoints authenticated by a shared secret key. The server stores one row per script (latest report only) in two new SQLite tables and serves the data server-side into `dashboard.html`. Chart.js renders line/bar panels; Bootstrap tables render table panels. Panel visibility is stored in localStorage.

**Tech Stack:** Flask, SQLite (existing), Chart.js 4.4.3 (existing), Bootstrap 5.3 (existing), openpyxl (existing in venv)

---

## File Map

| File | Change |
|------|--------|
| `db/core.py` | Add `script_reports` + `script_files` table creation to `init_db()` |
| `db/script_reports.py` | **New** — all DB ops for script reports and files |
| `db/__init__.py` | Re-export new functions |
| `config.py` | Add `REPORT_API_KEY` |
| `.env.example` | Add `REPORT_API_KEY=` entry |
| `app.py` | Add 4 new routes; extend `/dashboard` route |
| `templates/dashboard.html` | Add status strip, collapsible table, gear modal, script panel cards + JS |
| `D:/代码项目/GAODE/GAODE.py` | Add ~30 lines at end to push data + upload Excel |

---

## Task 1: DB Schema — add tables to `init_db()`

**Files:**
- Modify: `db/core.py` (the `init_db()` function, after the last `CREATE TABLE` block)

- [ ] **Step 1: Add `script_reports` and `script_files` tables to `init_db()`**

Open `db/core.py`. Inside `init_db()`, append these two `CREATE TABLE` statements inside the existing `conn.executescript("""...""")` call, after the last existing table:

```sql
            CREATE TABLE IF NOT EXISTS script_reports (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                script_name             TEXT    NOT NULL UNIQUE,
                status                  TEXT    NOT NULL CHECK(status IN ('ok','error')),
                error_message           TEXT,
                data_json               TEXT,
                pushed_at               TEXT    NOT NULL,
                expected_interval_hours REAL    NOT NULL DEFAULT 24
            );

            CREATE TABLE IF NOT EXISTS script_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                script_name TEXT    NOT NULL UNIQUE,
                filename    TEXT    NOT NULL,
                file_data   BLOB    NOT NULL,
                uploaded_at TEXT    NOT NULL
            );
```

- [ ] **Step 2: Verify tables are created**

```bash
cd "D:/代码项目/news_agent"
.venv/Scripts/python.exe -c "import db; print('OK')"
.venv/Scripts/python.exe -c "
import sqlite3; c = sqlite3.connect('news.db')
tables = c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print([t[0] for t in tables])
"
```

Expected output includes `script_reports` and `script_files`.

- [ ] **Step 3: Commit**

```bash
git add db/core.py
git commit -m "feat: add script_reports and script_files tables"
git push
```

---

## Task 2: DB Module `db/script_reports.py`

**Files:**
- Create: `db/script_reports.py`
- Modify: `db/__init__.py`

- [ ] **Step 1: Create `db/script_reports.py`**

```python
"""DB operations for external script reports and file uploads."""

import json
from datetime import datetime, timezone

from db.core import get_conn


def upsert_script_report(
    script_name: str,
    status: str,
    error_message: str | None,
    data_json: str | None,
    expected_interval_hours: float,
) -> None:
    """Insert or replace the latest report for a script."""
    pushed_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_reports
                   (script_name, status, error_message, data_json, pushed_at, expected_interval_hours)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(script_name) DO UPDATE SET
                   status                  = excluded.status,
                   error_message           = excluded.error_message,
                   data_json               = excluded.data_json,
                   pushed_at               = excluded.pushed_at,
                   expected_interval_hours = excluded.expected_interval_hours""",
            (script_name, status, error_message, data_json, pushed_at, expected_interval_hours),
        )


def get_all_script_reports() -> list[dict]:
    """Return all script reports with is_overdue flag and parsed panels."""
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM script_reports ORDER BY script_name"
        ).fetchall()
    result = []
    for r in rows:
        pushed_at = datetime.fromisoformat(r["pushed_at"])
        # Make naive timestamps timezone-aware (assume UTC)
        if pushed_at.tzinfo is None:
            pushed_at = pushed_at.replace(tzinfo=timezone.utc)
        hours_since = (now - pushed_at).total_seconds() / 3600
        is_overdue = hours_since > r["expected_interval_hours"]
        panels = json.loads(r["data_json"]) if r["data_json"] else []
        result.append({
            "script_name":             r["script_name"],
            "status":                  r["status"],
            "error_message":           r["error_message"],
            "panels":                  panels,
            "pushed_at":               r["pushed_at"],
            "expected_interval_hours": r["expected_interval_hours"],
            "is_overdue":              is_overdue,
        })
    return result


def upsert_script_file(script_name: str, filename: str, file_data: bytes) -> None:
    """Store (or replace) the latest Excel file for a script."""
    uploaded_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_files (script_name, filename, file_data, uploaded_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(script_name) DO UPDATE SET
                   filename    = excluded.filename,
                   file_data   = excluded.file_data,
                   uploaded_at = excluded.uploaded_at""",
            (script_name, filename, file_data, uploaded_at),
        )


def get_script_file(script_name: str) -> dict | None:
    """Return the stored file for a script, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT filename, file_data, uploaded_at FROM script_files WHERE script_name = ?",
            (script_name,),
        ).fetchone()
    if not row:
        return None
    return {"filename": row["filename"], "file_data": row["file_data"], "uploaded_at": row["uploaded_at"]}


def get_scripts_with_files() -> set[str]:
    """Return set of script names that have an uploaded file."""
    with get_conn() as conn:
        rows = conn.execute("SELECT script_name FROM script_files").fetchall()
    return {r["script_name"] for r in rows}
```

- [ ] **Step 2: Re-export from `db/__init__.py`**

Add the import block after the `from db.gpu_prices import ...` block:

```python
from db.script_reports import (
    upsert_script_report,
    get_all_script_reports,
    upsert_script_file,
    get_script_file,
    get_scripts_with_files,
)
```

And add to `__all__`:

```python
    # script reports
    "upsert_script_report", "get_all_script_reports",
    "upsert_script_file", "get_script_file", "get_scripts_with_files",
```

- [ ] **Step 3: Smoke-test the module**

```bash
cd "D:/代码项目/news_agent"
.venv/Scripts/python.exe -c "
import db, json
db.upsert_script_report('test-script', 'ok', None,
    json.dumps([{'type':'table','title':'T','headers':['A'],'rows':[['1']]}]), 24)
reports = db.get_all_script_reports()
assert len(reports) == 1
assert reports[0]['script_name'] == 'test-script'
assert reports[0]['is_overdue'] == False
print('script_reports OK')

db.upsert_script_file('test-script', 'test.xlsx', b'fake-bytes')
f = db.get_script_file('test-script')
assert f['filename'] == 'test.xlsx'
assert f['file_data'] == b'fake-bytes'
print('script_files OK')

# cleanup
import sqlite3; c = sqlite3.connect('news.db')
c.execute(\"DELETE FROM script_reports WHERE script_name='test-script'\")
c.execute(\"DELETE FROM script_files WHERE script_name='test-script'\")
c.commit()
print('cleanup OK')
"
```

Expected: three `OK` lines, no exceptions.

- [ ] **Step 4: Commit**

```bash
git add db/script_reports.py db/__init__.py
git commit -m "feat: add script_reports DB module"
git push
```

---

## Task 3: Config — add `REPORT_API_KEY`

**Files:**
- Modify: `config.py`
- Modify: `.env.example` (if it exists) or create note in `DEPLOY.md`

- [ ] **Step 1: Add to `config.py`**

Add after `SECRET_KEY`:

```python
REPORT_API_KEY = os.getenv("REPORT_API_KEY", "")
```

- [ ] **Step 2: Add to `.env.example`**

```bash
cd "D:/代码项目/news_agent"
grep -q "REPORT_API_KEY" .env.example 2>/dev/null || echo "REPORT_API_KEY=your-secret-key-here" >> .env.example
```

- [ ] **Step 3: Set the key in `.env` on local machine**

Choose any random string (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`) and add to `.env`:

```
REPORT_API_KEY=<generated-value>
```

- [ ] **Step 4: Commit**

```bash
git add config.py .env.example
git commit -m "feat: add REPORT_API_KEY config"
git push
```

---

## Task 4: API Routes in `app.py`

**Files:**
- Modify: `app.py`

Add all four new routes and update the existing `/dashboard` route. Place the new routes together, just before the existing `@app.route("/dashboard")` route.

- [ ] **Step 1: Add import for `REPORT_API_KEY` and `send_file`**

In `app.py`, find the config import line:
```python
from config import ADMIN_EMAIL, EMAIL_WHITELIST, SECRET_KEY
```
Change it to:
```python
from config import ADMIN_EMAIL, EMAIL_WHITELIST, REPORT_API_KEY, SECRET_KEY
```

Also ensure `send_file` and `io` are imported. Find the Flask imports line and add if missing:
```python
from flask import (Flask, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
import io
```

- [ ] **Step 2: Add the API key auth helper**

Add this function near the top of the route section (before the new routes):

```python
def _api_key_required():
    """Return a 403 response if the request's X-API-Key header doesn't match REPORT_API_KEY.
    Returns None if auth passes."""
    if not REPORT_API_KEY or request.headers.get("X-API-Key") != REPORT_API_KEY:
        return jsonify({"error": "forbidden"}), 403
    return None
```

- [ ] **Step 3: Add `POST /api/report`**

```python
@app.route("/api/report", methods=["POST"])
def api_report_push():
    denied = _api_key_required()
    if denied:
        return denied
    data = request.get_json(force=True, silent=True) or {}
    script_name = (data.get("script") or "").strip()
    if not script_name:
        return jsonify({"error": "script name required"}), 400
    status = data.get("status", "ok")
    if status not in ("ok", "error"):
        status = "error"
    error_message = data.get("error") or None
    expected_interval_hours = float(data.get("expected_interval_hours", 24))
    panels = data.get("panels") or []
    data_json = __import__("json").dumps(panels) if panels else None
    db.upsert_script_report(script_name, status, error_message, data_json, expected_interval_hours)
    return jsonify({"ok": True})
```

- [ ] **Step 4: Add `POST /api/report/<script>/excel`**

```python
@app.route("/api/report/<script_name>/excel", methods=["POST"])
def api_report_excel_upload(script_name):
    denied = _api_key_required()
    if denied:
        return denied
    if "file" not in request.files:
        return jsonify({"error": "no file field"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400
    file_data = f.read()
    db.upsert_script_file(script_name, f.filename, file_data)
    return jsonify({"ok": True})
```

- [ ] **Step 5: Add `GET /api/report/<script>/excel`**

```python
@app.route("/api/report/<script_name>/excel", methods=["GET"])
@login_required
def api_report_excel_download(script_name):
    row = db.get_script_file(script_name)
    if not row:
        return jsonify({"error": "not found"}), 404
    return send_file(
        io.BytesIO(row["file_data"]),
        download_name=row["filename"],
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
```

- [ ] **Step 6: Add `GET /dashboard/status`**

```python
@app.route("/dashboard/status")
@login_required
def dashboard_status():
    reports = db.get_all_script_reports()
    return jsonify([
        {
            "script_name":  r["script_name"],
            "status":       r["status"],
            "pushed_at":    r["pushed_at"],
            "is_overdue":   r["is_overdue"],
        }
        for r in reports
    ])
```

- [ ] **Step 7: Update `GET /dashboard` to pass script data**

Find the existing `/dashboard` route. It currently calls `render_template("dashboard.html", ...)`. Add `script_panels` and `scripts_with_files` to the template context:

```python
@app.route("/dashboard")
@login_required
def dashboard():
    script_panels = db.get_all_script_reports()
    scripts_with_files = db.get_scripts_with_files()
    return render_template(
        "dashboard.html",
        script_panels=script_panels,
        scripts_with_files=scripts_with_files,
    )
```

(Keep any existing GPU-price logic in the route if present — add the two new variables alongside what's already there.)

- [ ] **Step 8: Verify the server starts and routes exist**

```bash
cd "D:/代码项目/news_agent"
.venv/Scripts/python.exe -c "import app; print('import OK')"
```

Expected: `import OK`, no exceptions.

- [ ] **Step 9: Commit**

```bash
git add app.py config.py
git commit -m "feat: add script report API routes and dashboard status endpoint"
git push
```

---

## Task 5: Dashboard Template

**Files:**
- Modify: `templates/dashboard.html`

Three additions: (1) status strip + collapsible table before the GPU card, (2) gear modal, (3) script panel cards after the GPU card, plus supporting JS.

- [ ] **Step 1: Add status strip and collapsible table**

In `dashboard.html`, replace the opening `<div class="d-flex ...">` heading block with:

```html
<div class="d-flex align-items-center justify-content-between mb-3">
  <h4 class="mb-0 fw-bold">📊 数据 Dashboard</h4>
  <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="modal" data-bs-target="#customiseModal">
    ⚙ 自定义
  </button>
</div>

<!-- Status strip -->
{% if script_panels %}
<div class="d-flex flex-wrap gap-2 mb-2" id="status-strip">
  {% for s in script_panels %}
  <a href="#panel-{{ s.script_name | replace(' ', '-') }}"
     class="badge text-decoration-none
            {% if s.status == 'error' or s.is_overdue %}bg-danger{% else %}bg-success{% endif %}"
     style="font-size:.8rem;font-weight:500">
    {{ s.script_name }}
    · {{ s.pushed_at[:16] | replace('T',' ') }} UTC
  </a>
  {% endfor %}
</div>

<!-- Collapsible full status table -->
<div class="card mb-4">
  <div class="card-header py-2 px-3" style="cursor:pointer"
       data-bs-toggle="collapse" data-bs-target="#status-table-body">
    <span class="small fw-semibold">脚本状态详情</span>
    <span class="text-muted small ms-1">▸</span>
  </div>
  <div class="collapse" id="status-table-body">
    <table class="table table-sm mb-0">
      <thead class="table-light">
        <tr>
          <th>脚本</th><th>上次推送</th><th>预期间隔</th><th class="text-center">状态</th>
        </tr>
      </thead>
      <tbody>
        {% for s in script_panels %}
        <tr>
          <td class="small fw-medium">{{ s.script_name }}</td>
          <td class="small text-muted">{{ s.pushed_at[:16] | replace('T',' ') }} UTC</td>
          <td class="small text-muted">{{ s.expected_interval_hours | int }}h</td>
          <td class="text-center">
            {% if s.status == 'error' %}
              <span class="badge bg-danger">error</span>
            {% elif s.is_overdue %}
              <span class="badge bg-danger">overdue</span>
            {% else %}
              <span class="badge bg-success">ok</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}
```

- [ ] **Step 2: Add gear modal (before `{% endblock %}` of the content block)**

Add just before `{% endblock %}` (the content block end, before `{% block scripts %}`):

```html
<!-- Customise panels modal -->
<div class="modal fade" id="customiseModal" tabindex="-1">
  <div class="modal-dialog modal-sm">
    <div class="modal-content">
      <div class="modal-header py-2 px-3">
        <h6 class="modal-title mb-0">显示面板</h6>
        <button type="button" class="btn-close btn-sm" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body px-3 py-2">
        {% for s in script_panels %}
        <div class="form-check">
          <input type="checkbox" class="form-check-input panel-toggle"
                 id="toggle-{{ loop.index }}"
                 data-panel="panel-{{ s.script_name | replace(' ', '-') }}"
                 checked>
          <label class="form-check-label small" for="toggle-{{ loop.index }}">
            {{ s.script_name }}
          </label>
        </div>
        {% endfor %}
      </div>
      <div class="modal-footer py-2 px-3">
        <button class="btn btn-sm btn-primary" onclick="savePanelPrefs()">保存</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add script panel cards (after the GPU card `</div>`, before `{% endblock %}`)**

Add after the closing `</div>` of the GPU card section:

```html
<!-- Script data panels -->
{% for s in script_panels %}
<div class="card mb-4" id="panel-{{ s.script_name | replace(' ', '-') }}"
     data-script="{{ s.script_name }}">
  <div class="card-header d-flex align-items-center justify-content-between py-2">
    <div>
      <span class="fw-semibold">{{ s.script_name }}</span>
      <span class="ms-2 small text-muted">{{ s.pushed_at[:16] | replace('T',' ') }} UTC</span>
      {% if s.status == 'error' or s.is_overdue %}
        <span class="badge bg-danger ms-1">{{ 'error' if s.status == 'error' else 'overdue' }}</span>
      {% endif %}
    </div>
    {% if s.script_name in scripts_with_files %}
    <a href="/api/report/{{ s.script_name }}/excel"
       class="btn btn-sm btn-outline-secondary">⬇ Excel</a>
    {% endif %}
  </div>
  <div class="card-body">
    {% if s.panels %}
      {% for panel in s.panels %}
        <div class="mb-4">
          <div class="small fw-semibold text-muted mb-2">{{ panel.title }}</div>
          {% if panel.type in ('line', 'bar') %}
            <div style="position:relative;height:320px">
              <canvas id="chart-{{ s.script_name | replace(' ', '-') }}-{{ loop.index }}"></canvas>
            </div>
          {% elif panel.type == 'table' %}
            <div class="table-responsive">
              <table class="table table-sm table-hover mb-0">
                <thead class="table-light">
                  <tr>{% for h in panel.headers %}<th>{{ h }}</th>{% endfor %}</tr>
                </thead>
                <tbody>
                  {% for row in panel.rows %}
                  <tr>{% for cell in row %}<td class="small">{{ cell }}</td>{% endfor %}</tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p class="text-muted small mb-0">暂无数据面板。脚本运行后将自动显示。</p>
    {% endif %}
  </div>
</div>
{% endfor %}
```

- [ ] **Step 4: Add JavaScript to `{% block scripts %}`**

Append the following inside `{% block scripts %}`, after the existing GPU chart JS:

```javascript
// ---------------------------------------------------------------------------
// Script panels — Chart.js rendering + localStorage panel visibility
// ---------------------------------------------------------------------------

// Inject panel data from server (Jinja serialises to JSON)
const SCRIPT_PANELS = {{ script_panels | tojson }};

// Convert MM-DD string to day-of-year integer (1-indexed, non-leap)
function mmddToDoy(mmdd) {
  const monthDays = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const [m, d] = mmdd.split('-').map(Number);
  let doy = d;
  for (let i = 1; i < m; i++) doy += monthDays[i];
  return doy;
}

const MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const MONTH_LABELS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

const PANEL_COLORS = ['#0d6efd','#fd7e14','#198754','#dc3545','#6f42c1','#20c997'];

function renderScriptCharts() {
  SCRIPT_PANELS.forEach(script => {
    (script.panels || []).forEach((panel, idx) => {
      if (panel.type !== 'line' && panel.type !== 'bar') return;
      const canvasId = `chart-${script.script_name.replace(/ /g, '-')}-${idx + 1}`;
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;

      let datasets;
      if (panel.x_type === 'day_of_year') {
        datasets = (panel.datasets || []).map((ds, i) => ({
          label: ds.label,
          data: ds.data.map(pt => ({ x: mmddToDoy(pt.x), y: pt.y })),
          borderColor: PANEL_COLORS[i % PANEL_COLORS.length],
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 4,
          tension: 0.2,
          spanGaps: !!panel.span_gaps,
        }));
      } else {
        datasets = (panel.datasets || []).map((ds, i) => ({
          label: ds.label,
          data: ds.data.map(pt => ({ x: pt.x, y: pt.y })),
          borderColor: PANEL_COLORS[i % PANEL_COLORS.length],
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.3,
          spanGaps: !!panel.span_gaps,
        }));
      }

      const isDoy = panel.x_type === 'day_of_year';
      new Chart(canvas.getContext('2d'), {
        type: panel.type === 'bar' ? 'bar' : 'line',
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'top', labels: { boxWidth: 12, font: { size: 12 } } },
            tooltip: {
              callbacks: {
                title: items => isDoy
                  ? (() => {
                      const doy = items[0].parsed.x;
                      let m = 0;
                      while (m < 11 && MONTH_STARTS[m + 1] <= doy) m++;
                      return MONTH_LABELS[m] + ' day ' + (doy - MONTH_STARTS[m] + 1);
                    })()
                  : items[0].label,
              },
            },
          },
          scales: isDoy ? {
            x: {
              type: 'linear',
              min: 1, max: 365,
              ticks: {
                font: { size: 11 },
                callback: v => {
                  const i = MONTH_STARTS.indexOf(v);
                  return i >= 0 ? MONTH_LABELS[i] : '';
                },
                maxTicksLimit: 12,
                autoSkip: false,
              },
              grid: { color: 'rgba(0,0,0,0.04)' },
            },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
          } : {
            x: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
            y: { grid: { color: 'rgba(0,0,0,0.04)' }, ticks: { font: { size: 11 } } },
          },
        },
      });
    });
  });
}

// ---------------------------------------------------------------------------
// Panel visibility — localStorage
// ---------------------------------------------------------------------------
const PREFS_KEY = 'dashboardPanels';

function applyPanelPrefs() {
  const prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
  document.querySelectorAll('.card[data-script]').forEach(card => {
    const name = card.dataset.script;
    // Default visible; hide only if explicitly set false
    if (prefs[name] === false) card.style.display = 'none';
  });
  // Sync modal checkboxes
  document.querySelectorAll('.panel-toggle').forEach(cb => {
    const name = document.getElementById(
      cb.id.replace('toggle-', 'panel-' )
    )?.dataset?.script;
    if (name && prefs[name] === false) cb.checked = false;
  });
}

function savePanelPrefs() {
  const prefs = {};
  document.querySelectorAll('.panel-toggle').forEach(cb => {
    const panelId = cb.dataset.panel;
    const card = document.getElementById(panelId);
    if (card) {
      prefs[card.dataset.script] = cb.checked;
      card.style.display = cb.checked ? '' : 'none';
    }
  });
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  bootstrap.Modal.getInstance(document.getElementById('customiseModal'))?.hide();
}

// ---------------------------------------------------------------------------
// Status strip auto-refresh (every 60 s)
// ---------------------------------------------------------------------------
function refreshStatusStrip() {
  fetch('/dashboard/status')
    .then(r => r.json())
    .then(reports => {
      reports.forEach(r => {
        const badge = document.querySelector(
          `#status-strip a[href="#panel-${r.script_name.replace(/ /g, '-')}"]`
        );
        if (!badge) return;
        const bad = r.status === 'error' || r.is_overdue;
        badge.className = badge.className.replace(/bg-\w+/, bad ? 'bg-danger' : 'bg-success');
      });
    })
    .catch(() => {});
}

document.addEventListener('DOMContentLoaded', () => {
  applyPanelPrefs();
  renderScriptCharts();
  setInterval(refreshStatusStrip, 60000);
});
```

- [ ] **Step 5: Verify page loads**

```bash
cd "D:/代码项目/news_agent"
.venv/Scripts/python.exe -c "import app; print('template compile OK')"
```

Start the dev server and navigate to `/dashboard`. Confirm:
- Page loads without 500 errors
- GPU chart still works
- Status strip appears (empty if no reports yet)
- Gear button opens modal

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: dashboard status strip, script panels, gear modal"
git push
```

---

## Task 6: GaoDe Script Integration

**Files:**
- Modify: `D:/代码项目/GAODE/GAODE.py`

- [ ] **Step 1: Add `requests` to GAODE requirements**

```bash
cd "D:/代码项目/GAODE"
grep -q "^requests" requirements.txt || echo "requests" >> requirements.txt
venv/Scripts/pip install requests -q
```

- [ ] **Step 2: Add push code to `GAODE.py`**

Add the following block at the very end of `GAODE.py`, after `send_email(...)`:

```python
# ---------------------------------------------------------------------------
# Push to news_agent dashboard
# ---------------------------------------------------------------------------
import requests as _req
import openpyxl as _xl
from datetime import datetime as _dt

_API_BASE = "https://47.239.66.248"
_API_KEY  = "your-REPORT_API_KEY-here"   # match REPORT_API_KEY in news_agent .env

# Parse full Excel history into annual-comparison datasets
_wb = _xl.load_workbook(file_path, data_only=True)
_ws = _wb.active
_annual: dict = {}
for _row in _ws.iter_rows(min_row=2, values_only=True):
    if not _row[0]:
        continue
    _d   = _dt.fromisoformat(str(_row[0])[:19])
    _yr  = str(_d.year)
    _val = float(str(_row[2]).rstrip('%'))
    _annual.setdefault(_yr, []).append({"x": _d.strftime("%m-%d"), "y": _val})

_panels = [{
    "type":      "line",
    "title":     "路网高延时运行时间占比 — 年度对比",
    "x_type":    "day_of_year",
    "span_gaps": True,
    "datasets":  [{"label": yr, "data": pts} for yr, pts in sorted(_annual.items())],
}]

_ok = float(data[0][3].rstrip('%')) > 0
_sess = _req.Session()
_sess.verify = False

try:
    _sess.post(
        f"{_API_BASE}/api/report",
        headers={"X-API-Key": _API_KEY},
        json={
            "script": "gaode",
            "status": "ok" if _ok else "error",
            "expected_interval_hours": 24,
            "panels": _panels,
        },
        timeout=30,
    )
    with open(file_path, "rb") as _f:
        _sess.post(
            f"{_API_BASE}/api/report/gaode/excel",
            headers={"X-API-Key": _API_KEY},
            files={"file": ("GaoDe.xlsx", _f)},
            timeout=60,
        )
    print("Dashboard push OK")
except Exception as _e:
    print(f"Dashboard push failed (non-fatal): {_e}")
```

Replace `"your-REPORT_API_KEY-here"` with the actual key from `.env`.

- [ ] **Step 3: Test the push manually**

With the news_agent server running locally (`python app.py`), run:

```bash
cd "D:/代码项目/GAODE"
venv/Scripts/python.exe GAODE.py
```

Then visit `http://localhost:5000/dashboard` and confirm:
- `gaode` appears in the status strip as 🟢
- The 路网高延时运行时间占比 line chart renders with 4 coloured lines (2023–2026)
- The ⬇ Excel button appears and downloads `GaoDe.xlsx`

- [ ] **Step 4: Deploy server changes**

```bash
# On local machine
git push

# On server (47.239.66.248)
bash /opt/tofuhouse/news_agent/scripts/deploy.sh
```

- [ ] **Step 5: Final end-to-end test**

With the server deployed, run `GAODE.py` once more (or wait for its scheduled run) and confirm the dashboard at `https://47.239.66.248/dashboard` shows the gaode panel correctly.

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| `script_reports` table (UNIQUE on script_name, upsert) | Task 1 + Task 2 |
| `script_files` table (UNIQUE on script_name, upsert) | Task 1 + Task 2 |
| `POST /api/report` with API key auth | Task 4 |
| `POST /api/report/<script>/excel` | Task 4 |
| `GET /api/report/<script>/excel` (login_required) | Task 4 |
| `GET /dashboard/status` JSON endpoint | Task 4 |
| `REPORT_API_KEY` in config + `.env` | Task 3 |
| Status strip with green/red badges | Task 5 |
| Collapsible status table | Task 5 |
| Gear modal with localStorage panel visibility | Task 5 |
| Script panel cards (line, bar, table) | Task 5 |
| `x_type: day_of_year` — linear DOY axis, month tick labels | Task 5 |
| `span_gaps: true` support in Chart.js | Task 5 |
| Excel download button (only when file exists) | Task 5 |
| 60-second status strip auto-refresh | Task 5 |
| GaoDe.py integration — local parse + push | Task 6 |
| GPU card unchanged | dashboard.html additions are additive |
