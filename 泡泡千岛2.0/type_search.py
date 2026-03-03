"""
Type the search query into whichever element currently has focus, then submit.

Use when the cursor is already flashing in the search box — skips all
element discovery and just sends keys to the active element.

Run:
    python type_search.py
"""

import time

from appium import webdriver
from appium.options.common.base import AppiumOptions

import config

SEARCH_QUERY = "泡泡玛特"

_CAPS = {
    **config.APPIUM_CAPABILITIES,
    "appium:autoLaunch": False,
}


def main() -> None:
    options = AppiumOptions().load_capabilities(_CAPS)
    driver = webdriver.Remote(config.APPIUM_SERVER_URL, options=options)
    print(f"Connected (session {driver.session_id})")
    try:
        # mobile: type sends text through Appium's own IME — works for
        # React Native / Flutter inputs that reject native send_keys/paste.
        print(f"Typing '{SEARCH_QUERY}' via mobile: type ...")
        driver.execute_script("mobile: type", {"text": SEARCH_QUERY})
        print("Text injected.")
        time.sleep(0.5)

        driver.press_keycode(84)    # KEYCODE_SEARCH — submit
        print("Search submitted.")
        time.sleep(3)
    finally:
        driver.quit()
        print("Session closed.")


if __name__ == "__main__":
    main()
