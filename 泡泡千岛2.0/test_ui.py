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

import time

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy

import config
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

    time.sleep(2)
    driver.get_screenshot_as_file("output/after_first_result.png")
    print("  → Post-tap screenshot saved to output/after_first_result.png")
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
