import subprocess
import os
import sys
import logging
import time

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)

def kill_process_by_name(name: str):
    if sys.platform == "win32":
        try:
            # Using taskkill /F /T /IM
            # /F = force
            # /T = tree (child processes)
            # /IM = image name
            subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True, text=True)
            logger.info(f"Attempted to kill process: {name}")
        except Exception as e:
            logger.debug(f"Process {name} not found or could not be killed: {e}")

def kill_process_on_port(port: int):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                f"netstat -ano | findstr :{port}", 
                shell=True, capture_output=True, text=True
            )
            pids = set()
            for line in result.stdout.strip().split('\n'):
                if line and 'LISTENING' in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid != "0":
                            pids.add(pid)
            
            for pid in pids:
                logger.info(f"Killing process {pid} on port {port}")
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
        except Exception as e:
            logger.warning(f"Failed to kill process on port {port}: {e}")

def reset_env():
    """
    Hard reset of the environment:
    1. Kill Appium (node.exe)
    2. Kill mitmproxy (mitmdump.exe)
    3. Kill MuMu Emulator
    4. Kill ADB server
    """
    logger.info("--- [RESET] Starting environmental hard reset ---")
    
    # 1. Kill known components by name
    kill_process_by_name("node.exe")        # Appium
    kill_process_by_name("mitmdump.exe")    # mitmproxy
    kill_process_by_name("mitmproxy.exe")
    
    # MuMu processes
    kill_process_by_name("MuMuNxMain.exe")
    kill_process_by_name("MuMuManager.exe")
    kill_process_by_name("NemuPlayer.exe")
    kill_process_by_name("MuMuVMMVBoxHeadless.exe") # MuMu VM service
    
    # 2. Kill processes on specific ports just in case
    kill_process_on_port(4723) # Appium
    kill_process_on_port(8080) # mitmproxy
    kill_process_on_port(7555) # MuMu ADB
    kill_process_on_port(5037) # ADB Server
    
    # 3. Kill ADB server specifically
    kill_process_by_name("adb.exe")
    
    # Wait a bit for things to settle
    time.sleep(2)
    
    logger.info("--- [RESET] Environmental reset complete ---")

if __name__ == "__main__":
    # Configure basic logging if run directly
    logging.basicConfig(level=logging.INFO)
    reset_env()
