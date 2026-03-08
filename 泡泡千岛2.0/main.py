"""
泡泡千岛 2.0 — Main Orchestrator

Startup sequence:
  1. Start MuMu emulator and wait for ADB
  2. Start mitmproxy with the intercept addon
  3. Connect Appium session
  4. Open Postern and enable the VPN
  5. Open the target app (traffic now flows through mitmproxy)
  6. Run the scraping loop
  7. Shut everything down cleanly on exit or error

Usage:
    python main.py

Prerequisites:
  - Edit config.py to match your environment (MuMu path, target app, API pattern)
  - Appium server running: `appium`
  - mitmproxy installed: `pip install mitmproxy`
  - Postern installed on the emulator, configured to proxy through host:8080
"""

import logging
import signal
import sys
import time
import random

from appium.webdriver.common.appiumby import AppiumBy

import config
import screenshot

from emulator.mumu_controller import MuMuController
from proxy.mitm_server import MitmProxyServer
from automation.appium_client import AppiumSession
from automation.postern_controller import PosternController
from data.storage import DataStorage

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Graceful shutdown ─────────────────────────────────────────────────────────

def build_cleanup(
    emulator: MuMuController,
    proxy: MitmProxyServer,
    appium: AppiumSession,
):
    """Return a cleanup callable that captures live component references."""
    def cleanup():
        logger.info("--- Shutting down ---")
        for name, fn in [
            ("Appium", appium.quit),
            ("mitmproxy", proxy.stop),
            ("Emulator", emulator.stop_emulator),
        ]:
            try:
                fn()
            except Exception as exc:
                logger.warning("%s shutdown error: %s", name, exc)
    return cleanup


# ── Scraping logic ────────────────────────────────────────────────────────────

SEARCH_QUERY = "泡泡玛特"


def _tap_node(driver, node: dict) -> None:
    """Tap a UI node by resource-id, falling back to bounds centre."""
    rid = node.get("resource-id", "")
    if rid:
        driver.find_element(AppiumBy.ID, rid).click()
    else:
        cx, cy = screenshot.bounds_center(node["bounds"])
        driver.tap([(cx, cy)])


def run_scrape_loop(appium: AppiumSession, storage: DataStorage) -> None:
    driver = appium.driver

    # 1. Locate and tap the search input area
    logger.info("Dumping UI — looking for search input...")
    xml_path = screenshot.dump_ui_from_driver(driver)
    input_node = screenshot.find_search_input(xml_path)
    if input_node is None:
        raise RuntimeError("No search input found on current screen.")
    logger.info("Search input: %s  bounds=%s", input_node.get("resource-id"), input_node.get("bounds"))
    _tap_node(driver, input_node)
    time.sleep(1.5)

    # 2. Wait for search screen, then inject text via Appium IME
    # App is React Native — no native EditText exposed; mobile: type bypasses that.
    time.sleep(1.5)
    logger.info("Typing '%s' via mobile: type ...", SEARCH_QUERY)
    driver.execute_script("mobile: type", {"text": SEARCH_QUERY})
    time.sleep(0.5)

    # 3. Locate and tap the search button (React Native offset tap)
    logger.info("  Tapping search button at pixel (855, 68).")
    SEARCH_BTN_X = 855
    SEARCH_BTN_Y = 68
    driver.tap([(SEARCH_BTN_X, SEARCH_BTN_Y)])

    # 4. Wait for results to load, tap first result
    logger.info("  Waiting for search results to load...")
    time.sleep(3)
    driver.get_screenshot_as_file("output/results_page.png")
    
    FIRST_RESULT_X = 133
    FIRST_RESULT_Y = 385
    logger.info("  Tapping first result at pixel (%d, %d).", FIRST_RESULT_X, FIRST_RESULT_Y)
    driver.tap([(FIRST_RESULT_X, FIRST_RESULT_Y)])
    
    time.sleep(3)
    driver.get_screenshot_as_file("output/before_deeplink.png")

    # 5. Scroll, search for "玩具系列" via OCR, and tap
    logger.info("  Ensuring target string '玩具系列' is visible...")
    
    window = driver.get_window_size()
    w, h = window['width'], window['height']
    start_y = 1200
    end_y_initial = 1200 - int(h * random.uniform(0.15, 0.25))
    start_x = (w // 2) + random.randint(-50, 50)
    end_x = start_x + random.randint(-20, 20)
    
    logger.info("  Initial gentle scroll from y=%d to y=%d...", start_y, end_y_initial)
    driver.swipe(start_x, start_y, end_x, end_y_initial, random.randint(1200, 2000))
    time.sleep(random.uniform(1.0, 2.0))
    
    import ocr
    found_coords = None
    scroll_start_time = time.time()
    attempts = 0
    img_path = "output/search_scroll.png"
    
    while (time.time() - scroll_start_time) < 180:  # 3 minutes maximum
        img_path = f"output/search_scroll_{attempts}.png"
        driver.get_screenshot_as_file(img_path)
        
        blocks = ocr.ocr_all_text(img_path)
        for b in blocks:
            text = b["text"]
            if "玩具" in text and "系列" in text:
                coords = b["center"]
                logger.info("  → Found '%s' at pixel %s on attempt %d", text, coords, attempts)
                found_coords = coords
                break
                
        if found_coords:
            break
            
        logger.info("  → Not found on attempt %d. Scrolling down gently...", attempts)
        end_y_gentle = 1200 - int(h * random.uniform(0.15, 0.25))
        start_x = (w // 2) + random.randint(-50, 50)
        end_x = start_x + random.randint(-20, 20)
        # Gentle slow swipe taking between 1.5 - 2.5 seconds
        driver.swipe(start_x, start_y, end_x, end_y_gentle, random.randint(1500, 2500))
        time.sleep(random.uniform(1.0, 2.0))
        attempts += 1

    if found_coords:
        click_x = 830
        click_y = found_coords[1]
        
        import os
        JSONL_PATH = os.path.join(config.OUTPUT_DIR, config.JSONL_FILENAME)
        if os.path.exists(JSONL_PATH):
            os.remove(JSONL_PATH)
            logger.info("  [INFO] Cleared old results.jsonl cache before click.")

        logger.info("  Tapping row action at pixel (%d, %d).", click_x, click_y)
        driver.tap([(click_x, click_y)])
        
        logger.info("  Waiting 5 seconds for page load...")
        time.sleep(5)
        driver.get_screenshot_as_file("output/after_phase5.png")
        
        logger.info("  Retrieving information using mitmproxy logs...")
        import json
        
        items = []
        target_count = config.TARGET_SCRAPE_COUNT
        prev_len = -1
        stuck_count = 0
        scroll_count = 0
        
        while len(items) < target_count:
            current_seen = set()
            current_items = []
            
            if os.path.exists(JSONL_PATH):
                with open(JSONL_PATH, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            url = record.get("_url", "")
                            if "api.qiandao.com/treasure/spus/feed" in url:
                                list_data = record.get("data", {}).get("data", {}).get("list", [])
                                for item in list_data:
                                    item_id = item.get("id")
                                    if item_id and item_id not in current_seen:
                                        current_seen.add(item_id)
                                        current_items.append(item)
                        except json.JSONDecodeError:
                            continue
            
            items = current_items
            logger.info("  → Parsed %d/%d rows of data.", len(items), target_count)
            if len(items) >= target_count:
                break
                
            if len(items) == prev_len:
                stuck_count += 1
                if stuck_count >= 10:
                    logger.warning("  [WARN] No new items found for 10 consecutive scrolls. Assuming end of list.")
                    break
            else:
                prev_len = len(items)
                stuck_count = 0
                
            logger.info("  Scrolling to load more feed items...")
            
            start_x = (w // 2) + random.randint(-50, 50)
            end_x = start_x + random.randint(-30, 30)
            distance = int(h * random.uniform(0.60, 0.85))
            swipe_duration = random.randint(300, 600)
            
            for _retry in range(3):
                try:
                    driver.swipe(start_x, 1200, end_x, 1200 - distance, swipe_duration)
                    break
                except Exception as exc:
                    logger.warning("  [WARN] Swipe failed (%s), retrying in 2 seconds...", exc)
                    time.sleep(2)
                    
            scroll_count += 1
            if scroll_count % 10 == 0:
                try:
                    import subprocess
                    import gc
                    cmd = f'"{config.MUMU_ADB_PATH}" -s {config.ADB_ID} shell am send-trim-memory {config.TARGET_PACKAGE} RUNNING_CRITICAL'
                    subprocess.run(cmd, shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    gc.collect()
                    logger.info("  [INFO] 🧹 Triggered Android memory trim for %s and local python GC.", config.TARGET_PACKAGE)
                except Exception:
                    pass
            
            sleep_time = random.uniform(1.0, 2.0)
            logger.info("  → Waiting %.1fs to mimic reading speed and respect server constraints...", sleep_time)
            time.sleep(sleep_time)
            
        logger.info("  → Successfully scraped %d rows! (Records registered in storage: %d)", len(items), storage.record_count())
    else:
        logger.error("  [ERROR] Could not find '玩具系列' within 3 minutes.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Register signal handlers so Ctrl-C still triggers cleanup
    import signal
    import sys
    
    def handle_exit(*_args):
        logger.info("  [INFO] Received termination signal. Exiting gracefully.")
        sys.exit(0)
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_exit)

    logger.info("\n===========================================================")
    logger.info("       STARTING AUTOMATED SCRAPE RUN")
    logger.info("===========================================================\n")

    emulator = MuMuController()
    proxy    = MitmProxyServer()
    appium   = AppiumSession()
    storage  = DataStorage()
    postern  = None  # set in try block; referenced in finally for cleanup
    cleanup  = build_cleanup(emulator, proxy, appium)

    try:
        # 1. Emulator
        logger.info("=== Step 1: Start emulator ===")
        emulator.start_emulator()

        # 2. Proxy
        logger.info("=== Step 2: Start mitmproxy ===")
        proxy.start()

        # 3. Appium
        logger.info("=== Step 3: Connect Appium ===")
        appium.connect()

        # 4. Postern VPN — auto-connects on open
        logger.info("=== Step 4: Start Postern VPN ===")
        postern = PosternController(appium)  
        postern.start_postern()

        # 5. Target app
        logger.info("=== Step 5: Open target app ===")
        time.sleep(5)
        appium.open_app()
        time.sleep(5)   # wait for initial screen to load

        # 6. Scrape
        logger.info("=== Step 6: Run scrape loop ===")
        run_scrape_loop(appium, storage)

        import subprocess
        logger.info("=== Step 7: Parse resulting JSON to SQLite ===")
        subprocess.run([sys.executable, "parse_results.py"], check=True)
        
        limit = getattr(config, 'PROCESS_SKUS_LIMIT', 5)
        logger.info("=== Step 8: Detail Scrape with process_skus (Limit: %s) ===", limit)
        
        import process_skus
        total_db, success_db = process_skus.main()
        
        logger.info("=== Step 9: Dispatching Result Email ===")
        from automation.emailer import send_report_email
        
        import os
        attachment_path = "output/sku_lean_tracking.xlsx"
        send_report_email(success_count=success_db, failed_count=(total_db - success_db), attachment_path=attachment_path)

    except Exception as exc:
        logger.exception("Fatal error during run: %s", exc)
        sys.exit(1)
        
    finally:
        # Reverse startup order: target app → VPN → everything else
        logger.info("--- Shutting down components ---")
        try:
            if appium.driver is not None:
                appium.close_app()
        except Exception as exc:
            logger.warning("Target app close error: %s", exc)
            
        if postern is not None:
            try:
                postern.disable_vpn()
            except Exception as exc:
                logger.warning("Postern shutdown error: %s", exc)
                
        cleanup()
        logger.info("\n[CYCLE COMPLETE] Task finished execution successfully.")

if __name__ == "__main__":
    main()
