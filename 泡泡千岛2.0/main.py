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

    # 4. Locate and tap the search button (KEYCODE_SEARCH as fallback)
    logger.info("Dumping UI — looking for search button...")
    xml_path = screenshot.dump_ui_from_driver(driver)
    btn_node = screenshot.find_search_button(xml_path)
    if btn_node:
        logger.info("Search button: text='%s'  id=%s", btn_node.get("text"), btn_node.get("resource-id"))
        _tap_node(driver, btn_node)
    else:
        logger.info("Search button not found — pressing KEYCODE_SEARCH (84).")
        driver.press_keycode(84)
    time.sleep(3)

    logger.info("Search done. Records captured so far: %d", storage.record_count())


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    emulator = MuMuController()
    proxy    = MitmProxyServer()
    appium   = AppiumSession()
    storage  = DataStorage()
    postern  = None  # set in try block; referenced in finally for cleanup
    cleanup  = build_cleanup(emulator, proxy, appium)

    # Register signal handlers so Ctrl-C still triggers cleanup
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (cleanup(), sys.exit(0)))

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
        postern = PosternController(appium)  # noqa: F841 (referenced in finally)
        postern.start_postern()

        # 5. Target app
        logger.info("=== Step 5: Open target app ===")
        time.sleep(5)
        appium.open_app()
        time.sleep(5)   # wait for initial screen to load

        # 6. Scrape
        logger.info("=== Step 6: Run scrape loop ===")
        run_scrape_loop(appium, storage)

    except Exception as exc:
        logger.exception("Fatal error during run: %s", exc)
        sys.exit(1)
    finally:
        # Reverse startup order: target app → VPN → everything else
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


if __name__ == "__main__":
    main()
