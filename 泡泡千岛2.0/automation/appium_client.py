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

import time
import os
import config

logger = logging.getLogger(__name__)

class AppiumServer:
    """Wraps an Appium server subprocess."""
    def __init__(self, port: int = 4723) -> None:
        self.port = port
        self._process: Optional[subprocess.Popen] = None

    def start(self, wait_seconds: float = 12.0) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("Appium server is already running (pid=%d).", self._process.pid)
            return

        # --- Clean Slate: Kill existing process on port ---
        try:
            import sys
            if sys.platform == "win32":
                result = subprocess.run(
                    f"netstat -ano | findstr :{self.port}", 
                    shell=True, capture_output=True, text=True
                )
                for line in result.stdout.strip().split('\n'):
                    if line and 'LISTENING' in line:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            pid = parts[-1]
                            if pid != "0":
                                logger.info("Killing existing process %s on port %d", pid, self.port)
                                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            else:
                subprocess.run(f"lsof -ti:{self.port} | xargs kill -9", shell=True, capture_output=True)
        except Exception as e:
            logger.warning("Failed to clean up port %d: %s", self.port, e)
        # --------------------------------------------------

        project_root = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_path = os.path.join(project_root, "output", "appium.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        cmd = ["appium.cmd", "-p", str(self.port)]
        logger.info("Starting Appium server: %s  (log → %s)", " ".join(cmd), log_path)
        log_fh = open(log_path, "w", encoding="utf-8")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                shell=True
            )
        finally:
            log_fh.close()

        time.sleep(wait_seconds)
        if self._process.poll() is not None:
            raise RuntimeError(f"Appium exited immediately (rc={self._process.returncode}).")
        logger.info("Appium server listening on port %d (pid=%d).", self.port, self._process.pid)

    def stop(self) -> None:
        if not self._process or self._process.poll() is not None:
            return

        logger.info("Stopping Appium server (pid=%d).", self._process.pid)
        import sys
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._process.pid)], capture_output=True)
        else:
            self._process.terminate()
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
