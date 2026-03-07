# 泡泡千岛 2.0 - Automated Architecture

This project is a fully automated Android UI driving and mitmproxy interception pipeline for scraping the "泡泡千岛 2.0" application. It orchestrates the entire lifecycle of the host machine (mitmproxy, Appium) and the Android emulator (MuMu, target app, Postern VPN), handles human-like interactive scrolling, uses offline OCR to bypass React-Native UI restrictions, manages Android memory under heavy load, and gracefully spins everything down to prevent zombie processes.

## Architecture & Technology Stack
- **Python 3.9+** (in `.venv` virtual environment)
- **Appium (UiAutomator2)** for native interaction and screen polling.
- **EasyOCR** for visual inspection of elements that hide their text/IDs in the Android UI tree.
- **mitmproxy** (`mitmweb`/`mitmdump`) for invisible backend API packet interception.
- **Postern VPN** (Android App) for forcing target app API requests through the host machine's proxy.
- **Pandas / Openpyxl** for transforming nested JSON outputs into flat Excel deliverables.

---

## Quick Start & Usage

### 1. Production Run
To start a full run from a completely stopped state, run the orchestrator:
```powershell
.venv\Scripts\python.exe main.py
```
**What this does automatically:**
1. Launches the MuMu emulator and waits for ADB connection verification.
2. Starts the background `mitmdump` proxy server listening on port `8080`.
3. Establishes the Appium UiAutomator2 session.
4. Opens Postern VPN and flips the proxy connection switch ON.
5. Launches the target application (`tech.echoing.kuril`).
6. Enters the scraping phase: clicks the search bar, types "泡泡玛特", hits search, locates the results, uses OCR to locate "玩具系列", and then begins the infinitely-scrolling scrape loop while `addon.py` watches network traffic.
7. Gracefully tears down the app, VPN, proxy, appium, and emulator cleanly whether it succeeds or crashes.

### 2. Development Run
If you are tweaking the UI logic and do not want the emulator, VPN, and Proxy shutting down and restarting every time, you can leave the environment components open and work against:
```powershell
.venv\Scripts\python.exe test_ui.py
```
This bypasses backend/emulator initialization and runs *only* the Appium interaction workflow directly against whatever is currently open on the screen.

### 3. Parsing Results
After scraping concludes, the raw JSON packets will be safely sitting in `output/results.jsonl`. 
To convert this raw data into a clean, human-readable Excel sheet (`output/results.xlsx`):
```powershell
.venv\Scripts\python.exe parse_results.py
```

---

## Core Configuration & Files

- **`config.py`**: The central source of truth for paths, URIs, endpoints, and targets. This is where you configure `TARGET_SCRAPE_COUNT` (e.g., `1000` items) and `TARGET_URL_PATTERN` (e.g., `api.qiandao.com/treasure/spus/feed`).
- **`proxy/addon.py`**: The mitmproxy script. Looks mathematically for JSON blocks intercepted via the URL regex defined in `config.py` and silently appends them to disk in real-time.
- **`ocr.py`**: A computer vision module wrapping EasyOCR. Allows the scripts to capture the screen, parse English/Chinese characters from pixels, and calculate exact relative touchscreen (X, Y) coordinates to bypass React Native framework abstraction.
- **`screenshot.py`**: Generates and parses local `ui_dump.xml` pages cleanly. It checks `resource-id`, `bounds`, and UI text to perform layout heuristics.
- **`emulator/` & `automation/` modules**: Independent controllers providing safe abstractions over Appium, Postern UI manipulation, and MuMu process handling.

---

## Advanced Engineering Features

### Robust Zombie-Process Purging
On Windows environments, automated loops often crash, leaving isolated invisible `mitmdump` or `MuMu` subprocesses alive in the background which permanently lock Port 8080 or the ADB interface, entirely breaking subsequent runs. `main.py` utilizes hardcoded Windows `taskkill /F /T /PID` logic alongside context managers to aggressively sweep out these orphaned child threads inside the `finally:` block, guaranteeing a perfectly 0-state machine wipe every single time.

### Android Memory Management (Trim)
When dynamically fetching `1000+` rich image rows of JSON content via rapid Android native scrolling, the target app will rapidly consume system RAM causing Appium to suffer a fatal `socket hang up` crash. 
The scraping loop incorporates an aggressive `scroll_count`: Every 10 swipes, the host machine reaches directly into the OS using:
`adb shell am send-trim-memory tech.echoing.kuril RUNNING_CRITICAL`
Combined with python `gc.collect()`, this forces the Android memory renderer to dump offscreen cache memory immediately—bringing absolute stability back to the scraping engine.

### OCR Fuzzy Matching Guard
The scraper does not blindly click bounding boxes. Due to matching ambiguity (e.g., `玩具` vs. `玩具系列`), `test_ui.py` explicitly drops down into `ocr_all_text()` mode, looping over every detected bounding box in the screenshot, guaranteeing that it only fires a touchscreen tap event if exactly *both* vocabulary boundaries ("玩具" and "系列") exist physically within the returned machine-learning boundaries.

### Humanization Trajectory 
To evade server-side telemetry bot-detection, all repetitive motions pass through randomized mathematical models:
- **X-Coordinate Trajectory**: Thumb-path starting and ending coordinates randomly drift sideways `-50` to `+50` pixels per action.
- **Scroll Distance**: The drag offset randomly selects between `60%` and `85%` of the physical viewport height per attempt.
- **Swipe Velocity**: Finger pressure drag velocities are randomized between `300ms` and `600ms`.
- **Reading Decay**: Between-action sleep intervals are heavily pseudo-randomized between `1.0s` and `2.0s`.
