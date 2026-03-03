"""
Pixel tap navigator — attach to an existing Appium session and tap a target
pixel, then capture a before/after screenshot.

Prerequisites (must already be running/open):
  - MuMu emulator
  - Appium server  (`appium`)
  - Target app open on the screen you want to navigate FROM

Run:
    python try_deeplink.py

Look at output/before_tap.png, find the target element's centre pixel,
then set TAP_X / TAP_Y below and re-run.
"""

import time

from appium import webdriver
from appium.options.common.base import AppiumOptions

import config

# ── Tap target — read coordinates from output/before_tap.png ─────────────────
TAP_X = 450   # update after inspecting before_tap.png
TAP_Y = 200   # update after inspecting before_tap.png

_CAPS = {
    **config.APPIUM_CAPABILITIES,
    "appium:autoLaunch": False,
}


def main() -> None:
    options = AppiumOptions().load_capabilities(_CAPS)
    driver = webdriver.Remote(config.APPIUM_SERVER_URL, options=options)
    print(f"Connected (session {driver.session_id})")
    try:
        driver.get_screenshot_as_file("output/before_tap.png")
        print("Before screenshot saved → output/before_tap.png")

        print(f"Tapping pixel ({TAP_X}, {TAP_Y}).")
        driver.tap([(TAP_X, TAP_Y)])

        time.sleep(2)
        driver.get_screenshot_as_file("output/after_tap.png")
        print("After screenshot saved  → output/after_tap.png")
    finally:
        driver.quit()
        print("Session closed.")


if __name__ == "__main__":
    main()
