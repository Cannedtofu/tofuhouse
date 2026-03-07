# 泡泡千岛 2.0 - Automated Architecture

This project is a fully automated Android UI driving and mitmproxy interception pipeline for scraping the "泡泡千岛 2.0" application. It orchestrates the entire lifecycle of the host machine (mitmproxy, Appium) and the Android emulator (MuMu, target app, Postern VPN), handles human-like interactive scrolling, uses offline OCR to bypass React-Native UI restrictions, manages Android memory under heavy load, and gracefully spins everything down to prevent zombie processes.

## Architecture & Technology Stack
- **Python 3.9+** (in `.venv` virtual environment)
- **Appium (UiAutomator2)** for native interaction and screen polling.
- **EasyOCR** for visual inspection of elements that hide their text/IDs in the Android UI tree.
- **mitmproxy** (`mitmweb`/`mitmdump`) for invisible backend API packet interception.
- **Postern VPN** (Android App) for forcing target app API requests through the host machine's proxy.
- **Selenium & BeautifulSoup** for subsequent granular web-scraping on series and SKU detail pages.
- **SQLite3 & Pandas** for robust state-tracking, aggregation, and lean Excel reporting.

---

## Quick Start & Usage

### 1. The Master Pipeline Run
To run the entire end-to-end pipeline from a completely stopped state, simply run the orchestrator:
```powershell
.venv\Scripts\python.exe main.py
```
**What this single command does automatically:**
1. Launches the MuMu emulator and waits for ADB connection verification.
2. Starts the background `mitmdump` proxy server listening on port `8080`.
3. Establishes the Appium UiAutomator2 session.
4. Opens Postern VPN and flips the proxy connection switch ON.
5. Launches the target application (`tech.echoing.kuril`).
6. **Mobile Scraping Phase**: Clicks the search bar, types "泡泡玛特", hits search, locates the results, uses OCR to locate "玩具系列", and then begins the infinitely-scrolling Android scrape loop until it hits `TARGET_SCRAPE_COUNT` while `addon.py` dumps network packets to `results.jsonl`.
7. **Database Conversion Phase**: Automatically fires `parse_results.py` to convert the raw JSON into rows inside the robust `output/results.db` SQLite database, permanently stamping the time scraped.
8. **Granular Web Scraping Phase**: Automatically executes `process_skus.py`, spinning up a local Selenium browser in the background. It reads the top URLs from `results.db` and iteratively scrapes their deeply nested children pages into a pristine aggregate Excel sheet (`sku_lean_tracking.xlsx`) and an appended raw SQLite table (`sku_database.db`).
9. Gracefully tears down the app, VPN, proxy, appium, selenium, and emulator cleanly.

### 2. Development & Testing
If you are tweaking the UI logic and do not want the emulator, VPN, and Proxy shutting down and restarting every time, you can leave the environment components open and work against:
```powershell
.venv\Scripts\python.exe test_ui.py
```
This bypasses backend/emulator initialization and runs *only* the Appium interaction workflow directly against whatever is currently open on the screen.

---

## Core Configuration & Files

- **`config.py`**: The central source of truth for paths, URIs, endpoints, and targets. 
  - `TARGET_SCRAPE_COUNT` (e.g., `1000`): How many items the Android infinite-scroller should collect before stopping.
  - `PROCESS_SKUS_LIMIT` (e.g., `15`): Determines exactly how many of those scraped IDs the system should sequentially pipe into the detailed Selenium Web-Scraper (`process_skus.py`). Setting this to 10 will limit the deep-dive to the top 10 series found.
  - `TARGET_URL_PATTERN` (e.g., `api.qiandao.com/treasure/spus/feed`): The exact API endpoint mitmproxy watches for.
- **`proxy/addon.py`**: The mitmproxy script. Looks mathematically for JSON blocks intercepted via the URL regex defined in `config.py` and silently appends them to disk in real-time.
- **`process_skus.py`**: The granular web-scraping script. Employs fault-tolerant iterative-saving, network retries, and a Resume-Checker connected to `sku_database.db` so that if your computer crashes on page 1,999, running it again skips the first 1,999 pages automatically. 
- **`ocr.py`**: A computer vision module wrapping EasyOCR. Allows the scripts to capture the screen, parse English/Chinese characters from pixels, and calculate exact relative touchscreen (X, Y) coordinates.

---

## Advanced Engineering Features

### Robust Zombie-Process Purging
On Windows environments, automated loops often crash, leaving isolated invisible `mitmdump` or `MuMu` subprocesses alive in the background which permanently lock Port 8080 or the ADB interface, entirely breaking subsequent runs. `main.py` utilizes hardcoded Windows `taskkill /F /T /PID` logic alongside context managers to aggressively sweep out these orphaned child threads inside the `finally:` block, guaranteeing a perfectly 0-state machine wipe every single time.

### Incremental Zero-Loss Saving System
When processing 2000+ nested Web GUI pages sequentially via `process_skus.py`, holding data in memory carries catastrophic failure risk. The scraper utilizes absolute incremental persistence—writing the fully parsed SKU structures to the SQLite Database (`sku_database.db`) and Excel (`sku_lean_tracking.xlsx`) sequentially *per series loop*. If power is lost mid-scrape, exactly zero recorded items drop from memory. 

### Android Memory Management (Trim)
When dynamically fetching `1000+` rich image rows of JSON content via rapid Android native scrolling, the target app will rapidly consume system RAM causing Appium to suffer a fatal `socket hang up` crash. 
The scraping loop incorporates an aggressive `scroll_count`: Every 10 swipes, the host machine reaches directly into the OS using:
`adb shell am send-trim-memory tech.echoing.kuril RUNNING_CRITICAL`
Combined with python `gc.collect()`, this forces the Android memory renderer to dump offscreen cache memory immediately—bringing absolute stability back to the scraping engine.

### OCR Fuzzy Matching Guard
The scraper does not blindly click bounding boxes. Due to matching ambiguity (e.g., `玩具` vs. `玩具系列`), `test_ui.py` explicitly drops down into `ocr_all_text()` mode, looping over every detected bounding box in the screenshot, guaranteeing that it only fires a touchscreen tap event if exactly *both* vocabulary boundaries ("玩具" and "系列") exist physically within the returned machine-learning boundaries.
