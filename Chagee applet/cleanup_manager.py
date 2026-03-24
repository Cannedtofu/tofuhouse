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

def close_chagee_windows():
    print("Starting cleanup: Closing applet and search windows...")
    
    # Target window name
    TARGET_APPLET = "霸王茶姬"
    
    # 1. Close Applet Window
    max_retries = 3
    for attempt in range(max_retries):
        found_any = False
        for window in auto.GetRootControl().GetChildren():
            try:
                # Check for the applet window by name or specific class
                if TARGET_APPLET in window.Name or (window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != ""):
                    found_any = True
                    target_pid = window.ProcessId
                    win_name = window.Name
                    print(f"Attempt {attempt + 1}: Found Applet Window: '{win_name}' (PID: {target_pid})")
                    
                    window.SetActive()
                    time.sleep(1)
                    
                    # Method 1: Standard 'Close' Button in UI Tree (only if valid)
                    close_btn = window.ButtonControl(Name="关闭", searchDepth=3)
                    if not close_btn.Exists(0, 0):
                        close_btn = window.ButtonControl(Name="Close", searchDepth=3)
                    
                    if close_btn.Exists(0, 0):
                        rect = close_btn.BoundingRectangle
                        if rect.width() > 0 and rect.height() > 0:
                            print(f"  - Attempting to click '{close_btn.Name}' button...")
                            close_btn.Click()
                            time.sleep(2)
                            if not is_window_exists(TARGET_APPLET):
                                print("  - Window successfully closed via standard button.")
                                continue
                        else:
                            print(f"  - Standard button '{close_btn.Name}' found but has no size. Skipping Method 1.")
                    
                    # Method 2: Coordinate Click on the Top Right Capsule (Exit Icon)
                    # Coordinates verified from screenshots
                    rect = window.BoundingRectangle
                    click_x = rect.right - 40
                    click_y = rect.top + 30
                    print(f"  - Attempting capsule coordinate click at ({click_x}, {click_y})...")
                    auto.Click(click_x, click_y)
                    time.sleep(2)
                    
                    if not is_window_exists(TARGET_APPLET):
                        print("  - Window successfully closed via coordinate click.")
                        continue
                    
                    # Method 3: Alt+F4
                    print("  - Coordinate click failed or window still exists, trying Alt+F4...")
                    window.SetActive()
                    auto.SendKeys('{Alt}{F4}')
                    time.sleep(2)
                    
                    if not is_window_exists(TARGET_APPLET):
                        print("  - Window successfully closed via Alt+F4.")
                        continue

                    # Method 4: Taskkill by PID (Last resort)
                    if target_pid:
                        print(f"  - Forced methods: Killing PID {target_pid}...")
                        subprocess.run(["taskkill", "/F", "/PID", str(target_pid)], capture_output=True)
                        time.sleep(1)
                        if not is_window_exists(TARGET_APPLET):
                            print("  - Process killed successfully.")
                            break
                            
            except Exception as e:
                # If window was closed during the process, it might throw an error
                if not is_window_exists(TARGET_APPLET):
                    print(f"  - Window appears to have been closed during attempt.")
                    break
                else:
                    print(f"  - Error during close attempt on window: {e}")
        
        if not found_any:
            print(f"  - No more applet windows matching '{TARGET_APPLET}' found.")
            break
        
        if not is_window_exists(TARGET_APPLET):
            print("  - All target applet windows closed.")
            break
        else:
            print(f"  - One or more windows still open after attempt {attempt + 1}, retrying...")
            time.sleep(1)

    # 2. Close WeChat Search Result Window safely by exact Handle matching
    try:
        handle_file = "search_window_handle.txt"
        if os.path.exists(handle_file):
            with open(handle_file, "r") as f:
                handle_str = f.read().strip()
                if handle_str.isdigit():
                    target_handle = int(handle_str)
                    print(f"Closing specific Search Result Window (Handle: {target_handle}) used during init...")
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
                        print("Search Result Window already closed or not found.")
            # Clean up the handle file after attempts
            os.remove(handle_file)
        else:
            print("No saved search window handle found. Skipping generic search window cleanup to protect your other WeChat windows.")
    except Exception as e:
        print(f"Error during precise search window cleanup: {e}")

if __name__ == "__main__":
    close_chagee_windows()
