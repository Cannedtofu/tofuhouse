import uiautomation as auto
import time
import os
import subprocess

def is_window_exists(target_name, class_name="Chrome_WidgetWin_0"):
    """Globally check if any window matching the criteria still exists."""
    for window in auto.GetRootControl().GetChildren():
        try:
            if target_name in window.Name or (window.ClassName == class_name and window.Name != "微信" and window.Name != ""):
                return True
        except:
            pass
    return False

def close_guming_windows():
    print("Starting Guming cleanup: Closing applet and search windows...")
    TARGET_APPLET = "古茗"
    
    # 1. Close Applet Window
    max_retries = 3
    for attempt in range(max_retries):
        found_any = False
        for window in auto.GetRootControl().GetChildren():
            try:
                # Target Guming applet specifically
                if TARGET_APPLET in window.Name or (window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != ""):
                    found_any = True
                    target_pid = window.ProcessId
                    win_name = window.Name
                    print(f"Attempt {attempt + 1}: Found Guming Applet Window: '{win_name}' (PID: {target_pid})")
                    
                    if target_pid:
                        print(f"  - Force killing applet process (PID: {target_pid}) to prevent state inheritance...")
                        subprocess.run(["taskkill", "/F", "/PID", str(target_pid)], capture_output=True)
                        time.sleep(1)
                        if not is_window_exists(TARGET_APPLET):
                            print("  - Process killed successfully.")
                            break
            except Exception as e:
                if not is_window_exists(TARGET_APPLET):
                    break
                else:
                    print(f"  - Error closing window: {e}")
                    
        if not found_any:
            break
            
        if not is_window_exists(TARGET_APPLET):
            break
        else:
            time.sleep(1)

    # 2. Close WeChat Search window precisely using saved Handle
    try:
        handle_file = "search_window_handle.txt"
        if os.path.exists(handle_file):
            with open(handle_file, "r") as f:
                handle_str = f.read().strip()
                if handle_str.isdigit():
                    target_handle = int(handle_str)
                    print(f"Closing specific Search Result Window (Handle: {target_handle})...")
                    found = False
                    for window in auto.GetRootControl().GetChildren():
                        if window.NativeWindowHandle == target_handle:
                            found = True
                            window.SetActive()
                            time.sleep(0.5)
                            auto.SendKeys('{Alt}{F4}')
                            print("Search Result Window closed precisely.")
                            time.sleep(1)
                            break
                    if not found:
                        print("Search window already closed.")
            os.remove(handle_file)
    except Exception as e:
        print(f"Error during search window cleanup: {e}")

if __name__ == "__main__":
    close_guming_windows()
