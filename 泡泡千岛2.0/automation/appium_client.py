"""
Manages the Appium WebDriver session against the MuMu emulator.
Uses the UiAutomator2 driver.
"""

import logging
import subprocess
from typing import Optional

from appium import webdriver
from appium.options.common.base import AppiumOptions
from selenium.common.exceptions import WebDriverException

import config

logger = logging.getLogger(__name__)


class AppiumSession:
    """
    Wraps an Appium WebDriver session. The driver is created lazily on
    first use and can be cleanly shut down via quit().
    """

    def __init__(
        self,
        server_url: str = config.APPIUM_SERVER_URL,
        capabilities: dict = config.APPIUM_CAPABILITIES,
    ) -> None:
        self.server_url = server_url
        self.capabilities = capabilities
        self.driver: Optional[webdriver.Remote] = None

    # ── Session lifecycle ───────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Create the Appium session. Idempotent — does nothing if already connected.
        Raises WebDriverException on failure.
        """
        if self.driver is not None:
            logger.debug("Appium session already active.")
            return

        logger.info("Connecting to Appium at %s ...", self.server_url)
        options = AppiumOptions().load_capabilities(self.capabilities)
        try:
            self.driver = webdriver.Remote(self.server_url, options=options)
            logger.info("Appium session started (session_id=%s).", self.driver.session_id)
        except WebDriverException as exc:
            raise WebDriverException(
                f"Could not start Appium session on {self.server_url}: {exc}"
            ) from exc

    def quit(self) -> None:
        """End the WebDriver session and release the driver object."""
        if self.driver:
            logger.info("Quitting Appium session.")
            try:
                self.driver.quit()
            except WebDriverException as exc:
                logger.warning("Error while quitting Appium session: %s", exc)
            finally:
                self.driver = None

    # ── App control ─────────────────────────────────────────────────────────

    def open_app(
        self,
        package: str = config.TARGET_PACKAGE,
        activity: str = config.TARGET_ACTIVITY,
    ) -> None:
        """
        Launch (or bring to foreground) the specified app/activity via ADB am start.
        More reliable than the Appium mobile: startActivity script across driver versions.
        """
        self._require_driver()
        logger.info("Opening app: %s/%s", package, activity)
        subprocess.run(
            [config.MUMU_ADB_PATH, "-s", config.ADB_ID,
             "shell", "am", "start", "-n", f"{package}/{activity}"],
            capture_output=True,
        )

    def close_app(self, package: str = config.TARGET_PACKAGE) -> None:
        """Force-stop the specified package via ADB."""
        self._require_driver()
        logger.info("Force-stopping package: %s", package)
        subprocess.run(
            [config.MUMU_ADB_PATH, "-s", config.ADB_ID,
             "shell", "am", "force-stop", package],
            capture_output=True,
        )

    # ── Context manager support ─────────────────────────────────────────────

    def __enter__(self) -> "AppiumSession":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.quit()

    # ── Internals ───────────────────────────────────────────────────────────

    def _require_driver(self) -> None:
        if self.driver is None:
            raise RuntimeError("No active Appium session. Call connect() first.")
