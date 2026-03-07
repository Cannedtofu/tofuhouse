"""
Standalone UI interaction test — dynamically locates elements at runtime.

Prerequisites (all must already be running/open):
  - MuMu emulator
  - Appium server  (`appium`)
  - Target app open on screen

Run:
    python test_ui.py

Does NOT touch the emulator, mitmproxy, or Postern.
At each step the current UI tree is dumped and inspected so the code adapts
to layout changes without needing hardcoded resource-ids.
"""

"mitmweb --listen-host 0.0.0.0 --listen-port 8080 --scripts proxy/addon.py --ssl-insecure"

import time

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy

import config
import ocr
import random
import screenshot

_WAIT_TIMEOUT = 10  # seconds to wait for a new screen to load

SEARCH_QUERY = "泡泡玛特"

_CAPS = {
    **config.APPIUM_CAPABILITIES,
    "appium:autoLaunch": False,   # don't relaunch the app
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tap_node(driver, node: dict) -> None:
    """
    Tap a UI node.
    Prefers resource-id (more stable); falls back to bounds centre tap.
    """
    rid = node.get("resource-id", "")
    if rid:
        driver.find_element(AppiumBy.ID, rid).click()
    else:
        cx, cy = screenshot.bounds_center(node["bounds"])
        driver.tap([(cx, cy)])


# ── Core interaction ──────────────────────────────────────────────────────────

def run(driver) -> None:
    # ── Phase 1: locate and tap the search input area ─────────────────────────
    print("Dumping UI — looking for search input...")
    xml_path = screenshot.dump_ui_from_driver(driver)
    input_node = screenshot.find_search_input(xml_path)

    if input_node is None:
        raise RuntimeError("No search input found on current screen.")

    print(f"  → {input_node.get('class')}  id='{input_node.get('resource-id')}'  bounds={input_node.get('bounds')}")
    _tap_node(driver, input_node)
    time.sleep(1.5)

    # ── Phase 2: wait for search screen to load, then inject text ────────────
    # The app's search input is a React Native view — not a native EditText —
    # so send_keys/clipboard paste don't reach it.  mobile: type injects text
    # directly into whatever is focused, bypassing the element system.
    time.sleep(1.5)   # let the search screen finish loading
    print(f"Typing '{SEARCH_QUERY}' via mobile: type ...")
    driver.execute_script("mobile: type", {"text": SEARCH_QUERY})
    print("  → Text injected.")
    time.sleep(0.5)

    # ── Phase 3: tap the search button ────────────────────────────────────────
    # React Native doesn't expose the search button to UIAutomator2.
    # Tap the pixel just to the right of the search input bar instead.
    # The search container bounds are [110,45][810,93] on a 900-wide screen;
    # the "搜索" button sits in the remaining strip around x=855, y=68.
    # Adjust SEARCH_BTN_X / SEARCH_BTN_Y if the button shifts.
    SEARCH_BTN_X = 855
    SEARCH_BTN_Y = 68
    print(f"  Tapping search button at pixel ({SEARCH_BTN_X}, {SEARCH_BTN_Y}).")
    driver.tap([(SEARCH_BTN_X, SEARCH_BTN_Y)])

    # ── Phase 4: wait for results to load, tap first result ───────────────────
    # Results page is also React Native — pixel tap only.
    # FIRST_RESULT_X / FIRST_RESULT_Y are a starting guess for a 900×1600 screen;
    # check output/results_page.png and adjust if the tap misses.
    print("  Waiting for search results to load...")
    time.sleep(3)
    driver.get_screenshot_as_file("output/results_page.png")
    print("  → Results screenshot saved to output/results_page.png")

    FIRST_RESULT_X = 133   # horizontal centre of the screen
    FIRST_RESULT_Y = 385   # approx. top of first result card; increase if it misses
    print(f"  Tapping first result at pixel ({FIRST_RESULT_X}, {FIRST_RESULT_Y}).")
    driver.tap([(FIRST_RESULT_X, FIRST_RESULT_Y)])

    time.sleep(3)
    driver.get_screenshot_as_file("output/before_deeplink.png")
    print("  → Post-tap screenshot saved to output/before_deeplink.png")

    # ── Phase 5: scroll, search for "玩具系列" via OCR, and tap ────────────────
    print("  Ensuring target string '玩具系列' is visible...")
    
    # We need the screen dimensions to calculate relative scroll distances
    window = driver.get_window_size()
    w, h = window['width'], window['height']
    
    # Scroll #1: Scroll down by 2/3 of the screen, using fixed start_y of 1200
    start_y = 1200
    # Add random deviation to distance
    end_y_initial = 1200 - int(h * random.uniform(0.60, 0.70))
    
    # Add random jitter to swipe path
    start_x = (w // 2) + random.randint(-50, 50)
    end_x = start_x + random.randint(-20, 20)
    
    print(f"  Initial scroll (2/3 screen) from y={start_y} to y={end_y_initial}...")
    driver.swipe(start_x, start_y, end_x, end_y_initial, random.randint(800, 1400))
    time.sleep(random.uniform(2.0, 3.5))
    
    found_coords = None
    max_scrolls = 10
    img_path = "output/search_scroll.png"
    
    for i in range(max_scrolls):
        img_path = f"output/search_scroll_{i}.png"
        driver.get_screenshot_as_file(img_path)
        
        # Look for our keyword by inspecting all text blocks and enforcing both words
        blocks = ocr.ocr_all_text(img_path)
        for b in blocks:
            text = b["text"]
            if "玩具" in text and "系列" in text:
                coords = b["center"]
                print(f"  → Found '{text}' at pixel {coords} on attempt {i}")
                found_coords = coords
                break
                
        if found_coords:
            break
            
        print(f"  → Not found on attempt {i}. Scrolling down 1/2 screen...")
        
        # Scroll #2+: Scroll down by 1/2 screen, starting at 1200 again
        end_y_half = 1200 - int(h * random.uniform(0.40, 0.60))
        start_x = (w // 2) + random.randint(-50, 50)
        end_x = start_x + random.randint(-20, 20)
        driver.swipe(start_x, start_y, end_x, end_y_half, random.randint(800, 1400))
        time.sleep(random.uniform(2.0, 3.5))

    if found_coords:
        click_x = 830
        click_y = found_coords[1]
        
        # Draw a screenshot and mark click location with a circle box
        try:
            import cv2
            img = cv2.imread(img_path)
            if img is not None:
                cv2.circle(img, (click_x, click_y), 40, (0, 0, 255), 5) # Red circle with thickness 5
                marked_path = "output/marked_click.png"
                cv2.imwrite(marked_path, img)
                print(f"  → Marked click location saved to {marked_path}")
        except ImportError:
            print("  [WARN] cv2 not found, skipping circle drawing.")

        # Clear the old data so we don't instantly stop because of previous runs
        import os
        JSONL_PATH = os.path.join("output", "results.jsonl")
        if os.path.exists(JSONL_PATH):
            os.remove(JSONL_PATH)
            print("  [INFO] Cleared old results.jsonl cache before click.")

        print(f"  Tapping row action at pixel ({click_x}, {click_y}).")
        driver.tap([(click_x, click_y)])
        
        print("  Waiting 5 seconds for page load...")
        time.sleep(5)
        driver.get_screenshot_as_file("output/after_phase5.png")
        print("  → Pre-feed screenshot saved to output/after_phase5.png")
        
        # Start retrieving information using mitmproxy logs
        print("  Retrieving information using mitmproxy logs...")
        import json
        
        items = []
        
        target_count = config.TARGET_SCRAPE_COUNT
        
        # Keep track of previous item length to prevent infinite loops at the bottom
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
                            if "https://api.qiandao.com/treasure/spus/feed" in url:
                                list_data = record.get("data", {}).get("data", {}).get("list", [])
                                for item in list_data:
                                    item_id = item.get("id")
                                    if item_id and item_id not in current_seen:
                                        current_seen.add(item_id)
                                        current_items.append(item)
                        except json.JSONDecodeError:
                            continue
            
            items = current_items
            print(f"  → Parsed {len(items)}/{target_count} rows of data.")
            if len(items) >= target_count:
                break
                
            # Anti-infinite loop protection
            if len(items) == prev_len:
                stuck_count += 1
                if stuck_count >= 10:
                    print("  [WARN] No new items found for 10 consecutive scrolls. Assuming end of list.")
                    break
            else:
                prev_len = len(items)
                stuck_count = 0
                
            print("  Scrolling to load more feed items...")
            
            # 1. Randomize X coordinates slightly tracking human thumb paths
            start_x = (w // 2) + random.randint(-50, 50)
            end_x = start_x + random.randint(-30, 30)
            
            # 2. Randomize scroll distance (increased for faster traversal)
            distance = int(h * random.uniform(0.60, 0.85))
            
            # 3. Randomize swipe speed (decreased duration in ms for faster flicking)
            swipe_duration = random.randint(300, 600)
            
            # Robust swipe block: Appium sometimes drops the connection under high load (socket hang up)
            for _retry in range(3):
                try:
                    driver.swipe(start_x, 1200, end_x, 1200 - distance, swipe_duration)
                    break
                except Exception as exc:
                    print(f"  [WARN] Swipe failed ({exc}), retrying in 2 seconds...")
                    time.sleep(2)
                    
            scroll_count += 1
            if scroll_count % 10 == 0:
                try:
                    import subprocess
                    import gc
                    cmd = f'"{config.MUMU_ADB_PATH}" -s {config.ADB_ID} shell am send-trim-memory {config.TARGET_PACKAGE} RUNNING_CRITICAL'
                    subprocess.run(cmd, shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    gc.collect()
                    print(f"  [INFO] 🧹 Triggered Android memory trim for {config.TARGET_PACKAGE} and local python GC.")
                except Exception as e:
                    pass
            
            # 4. Human-like reading delay, randomize wait time 
            sleep_time = random.uniform(1.0, 2.0)
            print(f"  → Waiting {sleep_time:.1f}s to mimic reading speed and respect server constraints...")
            time.sleep(sleep_time)
            
        print(f"  → Successfully scraped {len(items)} rows!")

    else:
        print(f"  [ERROR] Could not find '玩具系列' after {max_scrolls} scrolls.")

    print("Done.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    options = AppiumOptions().load_capabilities(_CAPS)
    driver = webdriver.Remote(config.APPIUM_SERVER_URL, options=options)
    print(f"Connected (session {driver.session_id})")
    try:
        run(driver)
    finally:
        driver.quit()
        print("Session closed.")


if __name__ == "__main__":
    main()
