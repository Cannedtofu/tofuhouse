"""
Central configuration for 泡泡千岛 2.0.
All machine-specific and app-specific constants live here.
Edit this file before running main.py.
"""

# ── MuMu Emulator ─────────────────────────────────────────────────────────────
MUMU_EXE_PATH: list = [r"Z:\MuMu模拟器\nx_main\MuMuNxMain.exe", "-v", "0"]
# Use MuMu's bundled adb.exe — guaranteed to see its own device (also exposes 5555, 16384)
MUMU_ADB_PATH: str = r"Z:\MuMu模拟器\nx_main\adb.exe"
ADB_HOST: str = "127.0.0.1"
ADB_PORT: int = 7555   # confirmed working; MuMu NX primary port
ADB_ID: str = f"{ADB_HOST}:{ADB_PORT}"

# Maximum seconds to wait for ADB connection after launching emulator
EMULATOR_BOOT_TIMEOUT: int = 120
# Poll interval (seconds) while waiting for ADB
EMULATOR_POLL_INTERVAL: int = 5

# ── mitmproxy ─────────────────────────────────────────────────────────────────
MITM_HOST: str = "0.0.0.0"
MITM_PORT: int = 8080
MITM_ADDON_PATH: str = "proxy/addon.py"   # relative to project root

# URL substring the addon uses to identify the target API endpoint
# Set to "" for discovery mode (captures ALL JSON responses).
# Replace with a specific pattern once you know the API URL, e.g.:
#   TARGET_URL_PATTERN = "api.kuril.com/v1/products"
# Set to "" to capture ALL JSON responses (discovery mode).
# Replace with a specific substring once you've identified the right endpoint, e.g.:
#   TARGET_URL_PATTERN = "api.kuril.tech/v1/search"
TARGET_URL_PATTERN: str = "api.qiandao.com/treasure/spus/feed"

# ── Appium Server ─────────────────────────────────────────────────────────────
APPIUM_SERVER_URL: str = "http://127.0.0.1:4723"

# Desired capabilities for UiAutomator2 on MuMu emulator
APPIUM_CAPABILITIES: dict = {
    "platformName": "Android",
    "appium:automationName": "UiAutomator2",
    "appium:deviceName": "MuMuPlayer",
    "appium:udid": ADB_ID,
    "appium:newCommandTimeout": 300,
    "appium:noReset": True,
    "appium:autoGrantPermissions": True,
}

# ── Postern VPN App ───────────────────────────────────────────────────────────
POSTERN_PACKAGE: str = "com.tunnelworkshop.postern"
POSTERN_ACTIVITY: str = "com.tunnelworkshop.postern.PosternMain"

# ── Target App ────────────────────────────────────────────────────────────────
# Replace with the actual package and launch activity of the app being scraped
TARGET_PACKAGE: str = "tech.echoing.kuril"
TARGET_ACTIVITY: str = "tech.echoing.kuril.MainActivity"

# ── Data Storage ──────────────────────────────────────────────────────────────
OUTPUT_DIR: str = "output"
JSONL_FILENAME: str = "results.jsonl"
TARGET_SCRAPE_COUNT: int = 1000
