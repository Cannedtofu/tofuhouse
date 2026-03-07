"""
Controls the MuMu Android emulator process and ADB connectivity.
"""

import logging
import subprocess
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


class MuMuController:
    """
    Manages the MuMu emulator lifecycle: start, stop, and ADB connectivity checks.
    All ADB calls use subprocess directly; UI automation is delegated to AppiumSession.
    """

    def __init__(
        self,
        mumu_path: list = config.MUMU_EXE_PATH,
        adb_path: str = config.MUMU_ADB_PATH,
        adb_id: str = config.ADB_ID,
        boot_timeout: int = config.EMULATOR_BOOT_TIMEOUT,
        poll_interval: int = config.EMULATOR_POLL_INTERVAL,
    ) -> None:
        self.mumu_path = mumu_path
        self.adb_path = adb_path
        self.adb_id = adb_id
        self.boot_timeout = boot_timeout
        self.poll_interval = poll_interval
        self._process: Optional[subprocess.Popen] = None

    # ── Public API ──────────────────────────────────────────────────────────

    def start_emulator(self) -> None:
        """
        Launch MuMu and block until ADB reports 'connected' or timeout expires.
        Raises RuntimeError if the emulator does not become reachable in time.
        """
        logger.info("Launching MuMu emulator: %s", self.mumu_path)
        self._process = subprocess.Popen(self.mumu_path)

        logger.info("Waiting 20s for MuMu to initialize before polling ADB...")
        time.sleep(20)

        logger.info(
            "Waiting for ADB connection on %s (timeout=%ds)...",
            self.adb_id, self.boot_timeout,
        )
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            if self._adb_connect():
                logger.info("Emulator connected via ADB.")
                return
            time.sleep(self.poll_interval)

        raise RuntimeError(
            f"Emulator did not become reachable on {self.adb_id} "
            f"within {self.boot_timeout}s."
        )

    def stop_emulator(self) -> None:
        """Terminate the MuMu process if it was started by this controller."""
        if self._process and self._process.poll() is None:
            logger.info("Terminating MuMu emulator process (pid=%d).", self._process.pid)
            import sys
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._process.pid)], capture_output=True)
            else:
                self._process.terminate()
                
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Emulator did not stop gracefully; killing.")
                self._process.kill()
        else:
            logger.debug("Emulator process not tracked or already stopped.")

    def is_connected(self) -> bool:
        """Return True if ADB can reach the emulator right now."""
        result = subprocess.run(
            [self.adb_path, "devices"],
            capture_output=True,
            text=True,
        )
        return self.adb_id in result.stdout

    # ── Internals ───────────────────────────────────────────────────────────

    def _adb_connect(self) -> bool:
        """
        Run `adb connect <adb_id>` and return True if the output contains
        'connected' (covers both 'already connected' and 'connected to').
        """
        result = subprocess.run(
            [self.adb_path, "connect", self.adb_id],
            capture_output=True,
            text=True,
        )
        connected = "connected" in result.stdout.lower()
        logger.debug("adb connect stdout: %s", result.stdout.strip())
        return connected
