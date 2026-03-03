"""
Controls the Postern VPN app (com.tunnelworkshop.postern).

Behaviour: Postern auto-connects to its configured proxy as soon as the
app is opened — no button interaction is required.

start_postern() opens the app and blocks until the VPN tunnel is active
(detected via `ip route` on the device), then returns.
disable_vpn() force-stops the app, which tears down the tunnel.
"""

import logging
import subprocess
import time

import config
from automation.appium_client import AppiumSession

logger = logging.getLogger(__name__)

# How long to wait for the tun interface to appear after opening Postern
_VPN_CONNECT_TIMEOUT: int = 15
_VPN_POLL_INTERVAL: float = 1.0


class PosternController:
    """
    Opens/closes Postern and verifies VPN state via ADB ip route.
    Accepts an AppiumSession so the driver is shared with other controllers.
    """

    def __init__(self, session: AppiumSession) -> None:
        self.session = session

    # ── Public API ──────────────────────────────────────────────────────────

    def start_postern(self) -> None:
        """
        Open Postern (which auto-connects the VPN) and block until the
        tun interface is up. Raises RuntimeError on timeout.
        """
        logger.info("Opening Postern — VPN will auto-connect.")
        self.session.open_app(
            package=config.POSTERN_PACKAGE,
            activity=config.POSTERN_ACTIVITY,
        )
        self._wait_for_vpn_up()

    def disable_vpn(self) -> None:
        """
        Reverse the effect of start_postern():
          1. Force-stop Postern  (reverses open_app)
          2. Delete the tun interface  (reverses the VPN auto-connect)
          3. Clear any residual system proxy setting

        ip link delete does not touch the Wi-Fi adapter, so the ADB TCP
        connection to 127.0.0.1:7555 stays alive throughout.
        """
        logger.info("Force-stopping Postern to disable VPN.")
        self.session.close_app(package=config.POSTERN_PACKAGE)

        adb = [config.MUMU_ADB_PATH, "-s", config.ADB_ID, "shell"]
        subprocess.run(adb + ["settings", "put", "global", "http_proxy", ":0"],
                       capture_output=True, timeout=5)

        time.sleep(1.5)
        if not self.is_vpn_on():
            logger.info("VPN is now OFF.")
            return

        # Force-stop did not tear down the tun interface (common on emulators).
        # Delete it directly — adb shell on MuMu runs as root so this succeeds.
        logger.info("VPN still active; removing tun0 interface directly.")
        subprocess.run(adb + ["ip", "link", "delete", "tun0"],
                       capture_output=True, timeout=5)

        time.sleep(1.0)
        if self.is_vpn_on():
            logger.warning("VPN interface still present after tun0 removal.")
        else:
            logger.info("VPN is now OFF.")

    def is_vpn_on(self) -> bool:
        """
        Return True if a tun interface is up on the device.
        Uses `ip link show` — tun0 appears here even when Postern doesn't
        add a route entry (proxy-mode VPN, not full-tunnel).
        """
        result = subprocess.run(
            [config.MUMU_ADB_PATH, "-s", config.ADB_ID, "shell", "ip", "link", "show"],
            capture_output=True, text=True,
        )
        return "tun" in result.stdout.lower()

    # ── Internals ───────────────────────────────────────────────────────────

    def _wait_for_vpn_up(self) -> None:
        """Poll until tun interface appears or timeout expires."""
        deadline = time.monotonic() + _VPN_CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            if self.is_vpn_on():
                logger.info("VPN tunnel is active.")
                return
            time.sleep(_VPN_POLL_INTERVAL)
        raise RuntimeError(
            f"Postern VPN did not become active within {_VPN_CONNECT_TIMEOUT}s. "
            "Check that the proxy config in Postern is reachable from the emulator."
        )
