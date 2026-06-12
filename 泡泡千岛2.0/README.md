# 泡泡千岛 2.0 — Automated Pop Mart Market Intelligence Pipeline

An end-to-end automated data collection system that scrapes Pop Mart (泡泡玛特) collectible series data from the 千岛 (Qiandao) secondary market platform. The pipeline combines Android emulator automation, MITM proxy interception, OCR-guided UI navigation, and web scraping to produce a structured Excel report with market metrics (prices, demand, sales volumes) — delivered automatically by email.

---

## How It Works — Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py (Orchestrator)                   │
│                                                                 │
│  Step 1  MuMuController   →  Launch Android emulator + ADB     │
│  Step 2  MitmProxyServer  →  Start mitmproxy on port 8080       │
│  Step 3  AppiumServer     →  Start Appium UiAutomator2          │
│  Step 4  PosternController→  Open Postern VPN (auto-connects)   │
│  Step 5  AppiumSession    →  Launch target app (千岛)           │
│  Step 6  run_scrape_loop  →  Search, OCR scroll, collect data   │
│             └─ addon_db.py buffers API responses in memory      │
│             └─ COMMIT signal flushes buffer → results.db        │
│  Step 8  process_skus.py  →  Selenium scrape detail pages       │
│             └─ Saves to sku_database.db + sku_lean_tracking.xlsx│
│  Step 9  emailer.py       →  Email Excel report to recipients   │
│  Finally cleanup()        →  Tear down all components           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component | Technology | Role |
|---|---|---|
| Orchestration | Python 3.9+ | `main.py` drives all phases |
| Emulator | MuMu NX (Android) | Runs the 千岛 app |
| UI Automation | Appium / UiAutomator2 | Tap, type, swipe on emulator |
| Traffic Interception | mitmproxy (`mitmdump`) | Captures API JSON responses |
| VPN Routing | Postern (Android app) | Forces app traffic through proxy |
| OCR | EasyOCR (ch_sim + en) | Locates UI elements by pixel text |
| Web Scraping | Selenium + BeautifulSoup | Scrapes series detail pages |
| Storage | SQLite3 + Pandas | State tracking and aggregation |
| Reporting | openpyxl | Excel output (`sku_lean_tracking.xlsx`) |
| Delivery | SMTP (QQ Mail) | Emails final report to stakeholders |

---

## Project Structure

```
泡泡千岛2.0/
├── main.py                  # Master orchestrator — runs the full pipeline
├── config.py                # All machine-specific configuration (edit before running)
├── ocr.py                   # EasyOCR wrapper: finds text in screenshots by pixel coords
├── screenshot.py            # ADB/Appium UI dump + heuristic node finders
├── process_skus.py          # Selenium detail scraper for individual series pages
├── parse_results.py         # Utility: converts results.jsonl → results.db (legacy)
│
├── emulator/
│   └── mumu_controller.py   # MuMu emulator lifecycle: start, stop, ADB polling
│
├── proxy/
│   ├── addon_db.py          # mitmproxy addon: buffers API responses, commits to DB
│   └── addon.py             # Legacy addon (JSONL-based, superseded by addon_db.py)
│
├── automation/
│   ├── appium_client.py     # AppiumServer + AppiumSession: connect, open/close app
│   ├── postern_controller.py# Postern VPN: open app, wait for tun interface, teardown
│   ├── reset_env.py         # Hard reset: kills all zombie processes and locks ports
│   └── emailer.py           # Sends Excel report via QQ Mail SMTP
│
├── data/
│   └── storage.py           # DataStorage abstraction (filesystem helpers)
│
└── output/                  # All runtime artifacts (auto-created)
    ├── results.db           # Daily feed: SPU IDs captured from mobile scroll
    ├── results_history.db   # Cumulative archive of all ever-seen SPUs
    ├── sku_database.db      # Detail records from process_skus (one row per series/date)
    ├── sku_lean_tracking.xlsx # Final Excel report (appended daily)
    └── *.png                # Debug screenshots from each pipeline phase
```

---

## Quick Start

### Prerequisites

1. **MuMu NX Android Emulator** installed (paths configured in `config.py`)
2. **Postern** installed on the emulator, pre-configured to proxy through `<host-IP>:8080`
3. **千岛 app** (`tech.echoing.kuril`) installed on the emulator
4. **Appium** installed globally: `npm install -g appium && appium driver install uiautomator2`
5. **ChromeDriver** available (for `process_skus.py`). Default path: `C:\Program Files\python chrome\chrome-win\chromedriver.exe`
6. **Python dependencies**:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

### Configuration

Edit `config.py` before the first run:

```python
MUMU_EXE_PATH   = [r"Z:\MuMu模拟器\nx_main\MuMuNxMain.exe", "-v", "0"]
MUMU_ADB_PATH   = r"Z:\MuMu模拟器\nx_main\adb.exe"
ADB_PORT        = 7555           # MuMu NX primary ADB port

TARGET_URL_PATTERN  = "api.qiandao.com/treasure/spus/feed"  # API to intercept
TARGET_SCRAPE_COUNT = 1000       # Stop mobile scroll after N unique items
PROCESS_SKUS_LIMIT  = 300        # How many series to detail-scrape via Selenium
```

### Run

```powershell
# Full pipeline (emulator → scrape → detail → email)
.venv\Scripts\python.exe main.py

# Skip mobile scraping, use existing results.db (e.g., after a crash)
.venv\Scripts\python.exe main.py --skip-mumu
```

---

## Pipeline Phases in Detail

### Phase 1–5: Environment Setup
`reset_env()` first kills any zombie processes (node.exe, mitmdump.exe, MuMu processes) and clears ports 4723, 8080, 7555. Then each component starts in sequence: emulator → mitmproxy → Appium server → Appium session → Postern VPN.

### Phase 6: Mobile Scraping Loop (`run_scrape_loop`)
1. Dumps the Android UI tree via `driver.page_source` to find the search input.
2. Types "泡泡玛特" using `mobile: type` (bypasses React Native's hidden EditText).
3. Screenshots + **EasyOCR** scans for "玩具系列" text, scrolling down until found.
4. Enters the category and begins the infinite-scroll loop, swiping with randomised speed and distance to mimic human behaviour.
5. Every scroll cycle polls mitmproxy via `http://mitm.it/mitm_action=get_count` to track how many unique SPUs have been buffered.
6. Stops when `TARGET_SCRAPE_COUNT` is reached, 20 consecutive scrolls add nothing new, or the 110-scroll safety cap is hit.
7. Sends `mitm_action=commit_success` to flush the in-memory buffer to `results.db`.

### Phase 6 (mitmproxy side): `proxy/addon_db.py` — `DataInterceptor`
- Intercepts responses matching `TARGET_URL_PATTERN`.
- Parses `data.data.list[]` from the JSON payload, deduplicating by `id` in an in-memory dict.
- Only writes to disk when the `commit_success` signal is received — preventing corrupt partial writes.
- Writes to two databases simultaneously:
  - `results.db`: fresh daily queue (table dropped and recreated each run)
  - `results_history.db`: append-only archive tracking `first_found` and `last_seen` dates per SPU

### Phase 8: Detail Scraping (`process_skus.py`)
- Reads up to `PROCESS_SKUS_LIMIT` SPU IDs from `results.db`.
- Checks `sku_database.db` for IDs already processed today — resumes seamlessly after crashes.
- For each ID, Selenium loads `https://qiandao.com/spu?id={spu_id}` and BeautifulSoup extracts:
  - Series name, listing price
  - Community stats: views (浏览量), wants (想要), owns (拥有)
  - Transaction stats: avg price, paid count, sellers, buyers
  - Category tag (`mainTagDisplayName` from the feed data)
- Saves each record immediately (no batching) to both `sku_database.db` and `sku_lean_tracking.xlsx`.

### Phase 9: Email Report
Sends `sku_lean_tracking.xlsx` via QQ Mail SMTP to configured recipients with a summary of records processed.

---

## Output Files

| File | Format | Contents |
|---|---|---|
| `output/results.db` | SQLite | Today's SPU feed (id, name, tag, raw JSON) |
| `output/results_history.db` | SQLite | All-time SPU archive with first/last seen dates |
| `output/sku_database.db` | SQLite | Daily detail records (price, views, wants, owns) |
| `output/sku_lean_tracking.xlsx` | Excel | Aggregated tracking sheet — appended daily |
| `output/run.log` | Text | Full pipeline log |
| `output/appium.log` | Text | Appium server log |
| `output/search_scroll_N.png` | PNG | Debug screenshots from OCR scroll phase |

### Excel Column Reference (`sku_lean_tracking.xlsx`)

| Column | Description |
|---|---|
| 系列名 | Series/SPU name |
| 分类筛选 | Category filter used |
| 总SKU数 | Total SKU count |
| 隐藏款SKU数 | Hidden edition SKU count |
| 浏览量 | Total page views |
| 想要人数 | Number of people who "want" this |
| 拥有人数 | Number of people who own this |
| 付款人数 | Number of completed purchases |
| 正在出售 | Active sellers |
| 正在求购 | Active buyers |
| 交易价格_平均 | Average listing price |
| 成交均价_平均 | Average transaction price |
| 查询日期 | Date scraped |
| 系列URL | Series page URL |
| 主标签 | Primary category tag |

---

## Individual Module Usage

**OCR standalone demo:**
```powershell
.venv\Scripts\python.exe ocr.py output/before_tap.png 玩具系列
```

**UI dump (no Appium session running):**
```powershell
.venv\Scripts\python.exe screenshot.py
```

**Detail scrape only (requires existing results.db):**
```powershell
.venv\Scripts\python.exe process_skus.py --limit 50
```

**Environment reset only:**
```powershell
.venv\Scripts\python.exe automation/reset_env.py
```

**Parse legacy JSONL to DB:**
```powershell
.venv\Scripts\python.exe parse_results.py
```

---

## Key Design Decisions

### Buffered Commit Pattern
The mitmproxy addon holds all captured items in memory and only persists them when the orchestrator explicitly sends a commit signal. This ensures `results.db` is never written with partial data from an incomplete scroll session.

### OCR for React Native Apps
The target app is built with React Native, which does not expose native `EditText` IDs in the Android UI tree. EasyOCR reads screenshots directly to locate UI elements by their visible text, bypassing this limitation.

### Incremental Save in `process_skus.py`
Each series is saved to SQLite and Excel immediately after scraping — not at the end. Combined with the resume-checker (querying `sku_database.db` for today's already-processed IDs at startup), the scraper can recover from mid-run crashes with zero data loss.

### Environment Hard Reset
`reset_env()` runs before every pipeline start. It aggressively kills all processes by name and port (Appium/node.exe, mitmdump.exe, MuMu processes, adb.exe) to guarantee a clean slate, preventing the port-lock and ADB-conflict failures common in long-running automation environments on Windows.

---

## Scheduled Execution

A Windows batch script `qiandao_scheduler.bat` is included for Task Scheduler integration to run the pipeline on a daily schedule.

---

## Dependencies

```
mitmproxy>=9.0.0,<10.0.0
appium-python-client>=3.1.0
selenium>=4.0.0
openpyxl>=3.1.0
pandas>=2.0.0
easyocr>=1.7.0
```
> Note: EasyOCR downloads ~200 MB of model weights on first run.
