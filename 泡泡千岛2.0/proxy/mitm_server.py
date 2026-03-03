"""
Manages the mitmproxy subprocess on the host machine.
The addon script (proxy/addon.py) is injected via --scripts.
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


class MitmProxyServer:
    """
    Wraps a `mitmdump` subprocess so the rest of the application can treat
    the proxy as a simple start/stop service.
    """

    def __init__(
        self,
        host: str = config.MITM_HOST,
        port: int = config.MITM_PORT,
        addon_path: str = config.MITM_ADDON_PATH,
    ) -> None:
        self.host = host
        self.port = port
        self.addon_path = addon_path
        self._process: Optional[subprocess.Popen] = None

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, wait_seconds: float = 2.0) -> None:
        """
        Spawn mitmdump as a subprocess.
        wait_seconds gives the process time to bind the port before callers
        proceed. Raises RuntimeError if the process exits immediately.
        """
        if self._process and self._process.poll() is None:
            logger.warning("mitmproxy is already running (pid=%d).", self._process.pid)
            return

        cmd = [
            "mitmdump",
            "--listen-host", self.host,
            "--listen-port", str(self.port),
            "--scripts", self.addon_path,
            "--ssl-insecure",   # accept self-signed certs from the emulator side
        ]
        # Add project root to PYTHONPATH so addon.py can import config
        project_root = str(Path(__file__).parent.parent)
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        # Redirect to a log file — avoids stdout pipe-buffer blocking on Windows.
        # A full PIPE buffer causes mitmdump to stall on writes, stopping all traffic.
        log_path = os.path.join(project_root, "output", "mitmdump.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        logger.info("Starting mitmproxy: %s  (log → %s)", " ".join(cmd), log_path)
        log_fh = open(log_path, "w", encoding="utf-8")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=project_root,   # ensure relative --scripts path resolves correctly
            )
        finally:
            log_fh.close()  # parent's copy; subprocess keeps its own fd

        time.sleep(wait_seconds)

        if self._process.poll() is not None:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    out = f.read()
            except OSError:
                out = "<unavailable>"
            raise RuntimeError(
                f"mitmdump exited immediately (rc={self._process.returncode}).\n"
                f"Log ({log_path}):\n{out[:2000]}"
            )

        logger.info(
            "mitmproxy listening on %s:%d (pid=%d).",
            self.host, self.port, self._process.pid,
        )

    def stop(self) -> None:
        """Gracefully stop mitmdump; fall back to kill if needed."""
        if not self._process or self._process.poll() is not None:
            logger.debug("mitmproxy is not running.")
            return

        logger.info("Stopping mitmproxy (pid=%d).", self._process.pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            logger.warning("mitmproxy did not stop gracefully; killing.")
            self._process.kill()

    def is_running(self) -> bool:
        """Return True if the subprocess is alive."""
        return self._process is not None and self._process.poll() is None
