import uiautomation as auto
import time
import os
import subprocess

def close_chagee_windows():
    print("Starting cleanup: Closing applet and search windows...")
    
    # Target window name
    TARGET_APPLET = "霸王茶姬"
    
    # 1. Close Applet Window
    max_retries = 3
    for attempt in range(max_retries):
        found = False
        target_pid = None
        for window in auto.GetRootControl().GetChildren():
            # Check for the applet window by name or specific class
            if TARGET_APPLET in window.Name or (window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != ""):
                found = True
                target_pid = window.ProcessId
                print(f"Attempt {attempt + 1}: Closing Applet Window: '{window.Name}' (PID: {target_pid})")
                try:
                    window.SetActive()
                    time.sleep(0.5)
                    
                    # Method A: Coordinate Click (Top Right Capsule)
                    rect = window.BoundingRectangle
                    click_x = rect.right - 40
                    click_y = rect.top + 30
                    print(f"Trying click at ({click_x}, {click_y})")
                    auto.Click(click_x, click_y)
                    time.sleep(2)
                    
                    if not window.Exists(0, 0):
                        print("Window successfully closed via click.")
                        break
                    
                    # Method B: Alt+F4
                    print("Click failed or window still exists, trying Alt+F4...")
                    window.SetActive()
                    auto.SendKeys('{Alt}{F4}')
                    time.sleep(2)
                    
                    if not window.Exists(0, 0):
                        print("Window successfully closed via Alt+F4.")
                        break

                    # Method C: Taskkill by PID (Last resort)
                    if target_pid:
                        print(f"Standard methods failed, force-killing PID {target_pid}...")
                        subprocess.run(["taskkill", "/F", "/PID", str(target_pid)], capture_output=True)
                        time.sleep(1)
                        if not window.Exists(0, 0):
                            print("Process killed successfully.")
                            break
                        
                except Exception as e:
                    print(f"Error during close attempt: {e}")
        
        if not found:
            print(f"Target applet '{TARGET_APPLET}' not found.")
            break
        
        # Check if we still have the window
        still_exists = False
        for window in auto.GetRootControl().GetChildren():
            if TARGET_APPLET in window.Name:
                still_exists = True
                break
        
        if not still_exists:
            break
        else:
            print("Applet still open, retrying...")
            time.sleep(1)

    # 2. Close WeChat Search Result Window
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and window.Name == "微信":
            print(f"Closing Search Result Window: '{window.Name}'")
            try:
                window.SetActive()
                auto.SendKeys('{Alt}{F4}')
                time.sleep(1)
            except:
                pass

if __name__ == "__main__":
    close_chagee_windows()


