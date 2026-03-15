import uiautomation as auto
import time

def search_applet_only(applet_name):
    """Types the applet name into the search bar and submits."""
    print(f"Typing search query: {applet_name}")
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(1, 0):
        wechat_window = auto.WindowControl(Name="微信")
    
    wechat_window.SetActive()
    time.sleep(0.5)

    auto.SendKeys('{Ctrl}a{Delete}') 
    time.sleep(0.2)
    auto.SendKeys(applet_name)
    auto.SendKeys('{Enter}')
    print("Search query submitted. Please wait for results to load manually if needed.")

def find_search_result_window():
    """Finds and activates the window likely containing search results."""
    print("Searching for the result window (微信 / Chrome_WidgetWin_0)...")
    # Recently WeChat search opens in a Chrome_WidgetWin_0 window named '微信'
    # but the main window is 'WeChat' (Qt class)
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and ("微信" in window.Name or not window.Name):
            print(f"Found potential search window: '{window.Name}' | Class: {window.ClassName}")
            window.SetActive()
            time.sleep(0.5)
            return window
    
    # Fallback: check the main WeChat window if no separate window is found
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if wechat_window.Exists(1, 0):
        print("Falling back to main WeChat window...")
        wechat_window.SetActive()
        return wechat_window
        
    return None

def click_xiaochengxu_in_window(window):
    """Clicks at specific coordinates (151, 246) relative to the search window."""
    if not window:
        print("No window provided to click in.")
        return False
        
    print(f"Inside window '{window.Name}', clicking at relative coordinates (151, 246)...")
    
    # Ensure focus
    window.SetActive()
    time.sleep(0.5)
    
    # Calculate absolute coordinates based on window position
    rect = window.BoundingRectangle
    target_x = rect.left + 151
    target_y = rect.top + 246
    
    print(f"Window Rect: {rect}")
    print(f"Calculated Screen Coordinates: ({target_x}, {target_y})")
    
    # Perform the click
    auto.Click(target_x, target_y)
    
    print("Click performed. Waiting for applet to open...")
    time.sleep(5)
    return True

def click_xiaochengxu_button():
    """Combined logic to find the window and click the button."""
    window = find_search_result_window()
    if window:
        return click_xiaochengxu_in_window(window)
    print("Could not find a valid search result window.")
    return False

def search_and_open_applet(applet_name):
    """Full workflow: search, coordinate click, and wait for applet."""
    search_applet_only(applet_name)
    print("Waiting 5 seconds for results to load...")
    time.sleep(5)
    
    if click_xiaochengxu_button():
        print("Waiting for applet window to appear...")
        # Verify and wait for the applet window
        # Applet windows usually have class 'Chrome_WidgetWin_0' but different Name than main WeChat
        for i in range(10):
            for window in auto.GetRootControl().GetChildren():
                if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                    print(f"Applet window detected: '{window.Name}'")
                    return window
            time.sleep(1)
            
    print("Could not verify applet window opening.")
    return None


if __name__ == "__main__":
    # Placeholder for testing
    search_and_open_applet("霸王茶姬小程序")
