# Fix Plan — 泡泡千岛 2.0

**Goal**: Restore the full pipeline to working order, one module at a time.  
**Method**: Each phase has a test command, a clear pass/fail criterion, and a fix action before proceeding.  
**Stop rule**: Stop and ask the user for visual confirmation whenever a screen state needs to be verified.

---

## Known Issues (Pre-diagnosed)

| # | Location | Symptom | Root Cause |
|---|---|---|---|
| 1 | `proxy/addon_db.py:44` | 0 items buffered every run | Hardcoded JSON path `data.data.list` no longer matches API response structure |
| 2 | `main.py` (scroll loop) | Stale `results.db` used silently | Guard added — now raises `RuntimeError` on 0 items (already fixed) |

---

## Phase 1 — Environment & Infrastructure

**Objective**: Confirm all external tools can start cleanly.

### 1a. Environment reset

The actual pipeline Step 0 is `reset_env()`, which kills node.exe, mitmdump.exe, all MuMu processes, adb.exe, and frees ports 4723/8080/7555/5037. Run it first to guarantee a clean slate:

```bash
.venv\Scripts\python.exe automation/reset_env.py
```

**Pass**: Completes without error. All relevant processes are now dead.

### 1b. ADB + Emulator

```bash
# Start MuMu NX manually, then:
"Z:\MuMu模拟器\nx_main\adb.exe" connect 127.0.0.1:7555
"Z:\MuMu模拟器\nx_main\adb.exe" -s 127.0.0.1:7555 shell echo "adb ok"
```

**Pass**: `adb ok` printed.  
**Fail**: Connection refused → check MuMu is running and ADB bridge is enabled in MuMu settings.

### 1c. Appium Server

The code launches `appium.cmd` (not `appium`) and waits 12 seconds for it to be ready:

```bash
appium.cmd -p 4723
# Wait 12s (AppiumServer.start hardcodes wait_seconds=12.0), then in another terminal:
curl http://127.0.0.1:4723/status
```

**Pass**: JSON response with `{"ready": true, ...}`.  
**Fail**: Connection refused → check `appium.cmd` is in PATH (`npm list -g appium`), and UiAutomator2 driver is installed (`appium driver list --installed`).

### 1d. mitmproxy

```bash
.venv\Scripts\mitmdump.exe --version
.venv\Scripts\mitmdump.exe -p 8080 -s proxy/addon_db.py --set block_global=false
# Should print: "DataInterceptor (Buffered) watching pattern ..."
# and: "HTTP(S) proxy listening at *:8080"
# Ctrl-C to stop
```

**Pass**: Both lines appear, no Python exceptions.  
**Fail**: Import error → check `pip install mitmproxy` in venv; syntax error → fix the addon.

---

## Phase 2 — Appium UI Automation + Postern VPN

**Objective**: Verify Appium can connect, the app opens, the search/OCR navigation works, and Postern VPN correctly routes traffic through mitmproxy.

> **Note**: mitmproxy must be running during Phase 2c (start it with `mitmdump.exe -p 8080 -s proxy/addon_db.py --set block_global=false` in a separate terminal). `run_scrape_loop()` polls `http://mitm.it/mitm_action=get_count` on every scroll — the poll fails silently without mitmproxy but the stuck-counter increments every scroll, which is normal at this stage.

### 2a. Connect Appium session

```bash
# With MuMu running and appium.cmd running:
.venv\Scripts\python.exe -c "
from automation.appium_client import AppiumSession
s = AppiumSession()
s.connect()
s.open_app()
print('driver ready:', s.driver.session_id)
"
```

**Pass**: Session ID printed, app opens on emulator.  
**Fail**: `WinError 10061` → Appium server not yet ready; wait longer or check `appium.cmd` is still running.

### 2b. Postern VPN

```bash
# With Appium session active from 2a:
.venv\Scripts\python.exe -c "
from automation.appium_client import AppiumSession
from automation.postern_controller import PosternController
s = AppiumSession()
s.connect()
postern = PosternController(s)
postern.start_postern()   # Opens Postern app, blocks until tun interface appears
print('VPN on:', postern.is_vpn_on())
"
```

`PosternController.start_postern()` uses `am start com.tunnelworkshop.postern/...` and then polls `adb shell ip link show` waiting for `tun` to appear. Timeout is 15 seconds.

**Pass**: `VPN on: True` printed.  
**Fail timeout**: Postern didn't auto-connect → open Postern manually on the emulator and verify its proxy rule points to the host IP at port 8080.

### 2c. Search and OCR scroll to "玩具"

This step runs `run_scrape_loop()`, which does the ENTIRE flow: search → type → OCR scroll → enter feed → scroll loop → poll mitmproxy → commit. **The function will throw `RuntimeError("Mobile scrape captured 0 items")` at the end — this is expected and normal before the JSON path is fixed in Phase 3.** The goal here is only to confirm the UI navigation reaches the scroll loop.

```bash
# With mitmproxy running in a separate terminal:
.venv\Scripts\python.exe -c "
from automation.appium_client import AppiumSession
from data.storage import DataStorage
from main import run_scrape_loop
import time
s = AppiumSession()
s.connect()
s.open_app()
time.sleep(5)
try:
    run_scrape_loop(s, DataStorage())
except RuntimeError as e:
    print('[EXPECTED] RuntimeError:', e)
"
```

Watch for:
- `Tapping search button at pixel (855, 68)` — confirm in screenshot that this hits the button
- `Found '玩具...' at pixel ...` — OCR matched a text block containing "玩具"
- `Tapping row action at pixel (830, Y)` — entering the feed
- `Scrolling (Scroll N, Stuck: N/20)` — scroll loop running

**Pass**: Reaches and runs the scroll loop. `RuntimeError` fires at the end — that is expected.  
**Fail at search input**: `find_search_input` returned None → open `output/ui_dump.xml`, find the search bar node, update `screenshot.find_search_input()`.  
**Fail at search button**: Pixel `(855, 68)` misses the button → check screenshot `output/results_page.png`, find new button position, update `main.py:117-118`.  
**Fail at OCR**: No text containing "玩具" found within 3 minutes → check screenshot `output/search_scroll_0.png`. The category label may have changed in the app.

> **STOP — visual check**: After tapping the first search result (step 4 in the scrape loop), share `output/before_deeplink.png` to confirm we're on the right page before the OCR scroll begins.

---

## Phase 3 — Fix the API JSON Parsing (Critical Bug)

**Objective**: Discover the actual JSON structure of the `spus/feed` response and update the parser.

### 3a. Capture a raw response

With mitmproxy running and Postern VPN active (from Phase 2b), navigate to the feed in the app and let it scroll. The debug logging already added to `addon_db.py` will print the actual keys when 0 items are parsed. Check `output/mitmdump.log` for lines like:

```
0 items parsed from https://api.qiandao.com/treasure/spus/feed 
  — top-level keys: ['code', 'data', 'msg'] 
  | payload['data'] keys: ['list', 'pagination']
```

If the log isn't clear enough, capture a raw sample using a temporary diagnostic script. Create `proxy/dump_json.py` (delete after use):

```python
# proxy/dump_json.py  — temporary diagnostic, delete after use
import json, os

class Dumper:
    def response(self, flow):
        if "spus/feed" in flow.request.pretty_url:
            raw = flow.response.get_text(strict=False)
            try:
                obj = json.loads(raw)
                os.makedirs("output", exist_ok=True)
                with open("output/spus_feed_sample.json", "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
                print("=== WRITTEN to output/spus_feed_sample.json ===")
                print("Top-level keys:", list(obj.keys()))
            except Exception as e:
                print("Parse error:", e)

addons = [Dumper()]
```

Run it:
```bash
.venv\Scripts\mitmdump.exe -p 8080 --set block_global=false -s proxy/dump_json.py
# Then scroll the app to trigger a few spus/feed calls
```

**Pass**: `output/spus_feed_sample.json` created. Read it to find the correct list path.

### 3b. Fix the parser

Once the actual JSON path is known, update `proxy/addon_db.py` line 44:

```python
# CURRENT (broken):
items = payload.get("data", {}).get("data", {}).get("list", [])

# EXAMPLE FIX if path is data.list:
items = payload.get("data", {}).get("list", [])

# EXAMPLE FIX if path is data.result.items:
items = payload.get("data", {}).get("result", {}).get("items", [])
```

Also confirm each item still has an `"id"` field (used as the dedup key in line 57). Update if renamed.

**Pass**: Running mitmproxy with the updated addon and scrolling the app produces:
```
Buffered 20 items in memory. Total unique items: 20
Buffered 20 items in memory. Total unique items: 38
```

---

## Phase 4 — Full Pipeline Run

**Objective**: Run the entire pipeline end-to-end and verify fresh data flows through all steps.

Note: `main.py` has no flag to run only the mobile scrape steps — it always runs all 9 steps including `process_skus` (Step 8) and email (Step 9). There is only `--skip-mumu` which skips Steps 1-7 and uses an existing `results.db`. Run the full pipeline:

```bash
.venv\Scripts\python.exe main.py
```

Watch the console for these milestones in order:
1. `[RESET] Starting environmental hard reset` — reset_env ran
2. `Step 1: Start emulator` → `Step 4: Start Postern VPN` → VPN tunnel is active
3. `Buffered N items in memory. Total unique items: N` — mitmproxy is capturing
4. `[PROGRESS] N unique items captured` — count growing
5. `[SUCCESS] Target count N reached` or `[STOP] No new items in 20 scrolls` — scroll loop ended
6. `[SUMMARY] Total SPUs logged from MuMu: N` — N must be > 0
7. `process_skus` output: series names, price, views being printed
8. `Email sent successfully`

**Pass**: All 8 milestones reached, `results.db` and `sku_lean_tracking.xlsx` have today's date.  
**Fail at step 3 (0 items)**: `RuntimeError` fires from the guard. Re-check Phase 3b fix.  
**Fail at step 7 (process_skus crashes)**: CSS selectors on `qiandao.com` have changed — proceed to Phase 5 to diagnose.

---

## Phase 5 — process_skus.py (Selenium Detail Scraper)

**Objective**: Confirm the Selenium scraper can still parse `qiandao.com/spu?id=...` pages with the current site structure. Run this phase if Phase 4 fails at Step 8.

### 5a. Single-ID smoke test

```bash
.venv\Scripts\python.exe -c "
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import sqlite3, time

options = Options()
try:
    service = Service(r'C:\Program Files\python chrome\chrome-win\chromedriver.exe')
    driver = webdriver.Chrome(service=service, options=options)
except:
    driver = webdriver.Chrome(options=options)
driver.set_page_load_timeout(15)

conn = sqlite3.connect('output/results.db')
row = conn.execute('SELECT id FROM feed_results LIMIT 1').fetchone()
conn.close()
spu_id = row[0]
print('Testing SPU ID:', spu_id)

driver.get(f'https://qiandao.com/spu?id={spu_id}')
time.sleep(3)
driver.get_screenshot_as_file('output/phase5_spu_page.png')
soup = BeautifulSoup(driver.page_source, 'html.parser')

name    = soup.find('div', class_='single-spu-name')
price   = soup.find('div', class_='spu-price')
want    = soup.find('div', class_='want_info')
wrapper = soup.find('div', class_='wrapper')

print('name:',     name.get_text(strip=True)         if name    else 'NOT FOUND')
print('price:',    price.get_text(strip=True)         if price   else 'NOT FOUND')
print('want_info:',want.get_text(strip=True)          if want    else 'NOT FOUND')
print('wrapper:',  wrapper.get_text(strip=True)[:100] if wrapper else 'NOT FOUND')
driver.quit()
"
```

**Pass**: All four fields print non-empty values.  
**Fail — `NOT FOUND` for one or more fields**: The site's CSS class names have changed.

> **STOP — visual check**: Share `output/phase5_spu_page.png` so we can see the current page layout and identify the new HTML selectors to update in `process_skus.py`.

### 5b. Limited run

```bash
.venv\Scripts\python.exe process_skus.py --limit 5
```

**Pass**: 5 rows appear in `sku_lean_tracking.xlsx` with non-zero data.  
**Fail**: Check specific error printed per-record.

---

## Phase 6 — Scheduler Reliability

**Objective**: Ensure the Windows Task Scheduler run no longer silently produces stale output.

After Phase 4 passes, review `scheduler_last_run.log` after the next scheduled run:

- Must NOT contain `Total SPUs logged from MuMu: 0`
- Must NOT contain `Mobile scrape captured 0 items`
- Must contain `[SUCCESS] Target count N reached` or `[STOP] No new items in 20 scrolls` with N > 0

---

## Quick Reference — Files to Edit

| Issue | File | Line | What to change |
|---|---|---|---|
| JSON path broken | `proxy/addon_db.py` | 44 | Update `data.data.list` to correct path found in Phase 3 |
| Item ID field renamed | `proxy/addon_db.py` | 57 | Update `item.get("id")` if key changed |
| Search button pixel | `main.py` | 117–118 | Update `SEARCH_BTN_X`, `SEARCH_BTN_Y` if UI shifted |
| First result pixel | `main.py` | 126–128 | Update `FIRST_RESULT_X`, `FIRST_RESULT_Y` if UI shifted |
| Search input heuristic | `screenshot.py` | 101–126 | Update class/attribute matching if app UI changed |
| CSS selectors | `process_skus.py` | 161, 174, 179, 187 | Update class names if site HTML changed |

---

## Status Tracker

- [x] Phase 1a — reset_env cleans up all stale processes
- [x] Phase 1b — ADB connects to emulator
- [x] Phase 1c — Appium server responds on port 4723
- [x] Phase 1d — mitmproxy loads addon without errors
- [x] Phase 2a — Appium session connects, app opens
- [x] Phase 2b — Postern VPN active (tun interface up)
- [x] Phase 2c — OCR scroll finds "玩具", enters scroll loop (RuntimeError at end is expected)
- [x] Phase 3a — Raw `spus/feed` JSON captured, correct path: `data.list`
- [x] Phase 3b — JSON path fixed in `addon_db.py`, 264 items buffered and committed
- [ ] Phase 4  — Full pipeline completes end-to-end via `main.py`
- [x] Phase 5a — Selenium parses SPU pages correctly (CSS selectors still valid)
- [x] Phase 5b — `process_skus.py --limit 5` produces valid Excel rows (5/5 OK)
- [ ] Phase 6  — Scheduled run log shows non-zero capture
