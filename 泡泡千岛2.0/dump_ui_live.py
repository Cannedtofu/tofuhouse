"""
Dump the current app screen UI hierarchy to output/ui_dump.xml.

Prerequisites (must already be running):
  - MuMu emulator
  - Appium server  (`appium`)
  - Target app open on the screen you want to inspect

Run:
    python dump_ui_live.py

The XML is saved to output/ui_dump.xml and the path is printed.
Does NOT touch the emulator or interact with the app in any way.
"""

from appium import webdriver
from appium.options.common.base import AppiumOptions

import config
import screenshot

_CAPS = {
    **config.APPIUM_CAPABILITIES,
    "appium:autoLaunch": False,
}


def main() -> None:
    options = AppiumOptions().load_capabilities(_CAPS)
    driver = webdriver.Remote(config.APPIUM_SERVER_URL, options=options)
    print(f"Connected (session {driver.session_id})")
    try:
        path = screenshot.dump_ui_from_driver(driver)
        print(f"UI hierarchy saved → {path}")

        img_path = "output/screenshot.png"
        driver.get_screenshot_as_file(img_path)
        print(f"Screenshot saved    → {img_path}")
    finally:
        driver.quit()
        print("Session closed.")


if __name__ == "__main__":
    main()
