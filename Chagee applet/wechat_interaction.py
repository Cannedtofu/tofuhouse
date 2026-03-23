import uiautomation as auto
import time

def focus_wechat_and_open_search():
    """Focuses the main WeChat window and opens the search bar to find an applet."""
    print("Looking for WeChat main window...")
    wechat_window = None
    # Try finding by ClassName first as it's more specific to this Qt version
    try:
        wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon", searchDepth=1)
        if not wechat_window.Exists(1, 0):
            # Fallback to Name if ClassName fails
            wechat_window = auto.WindowControl(Name="微信", searchDepth=1)
    except Exception as e:
        print(f"Error initializing window control: {e}")

    if not wechat_window or not wechat_window.Exists(3, 1):
        print("WeChat main window not found. Please ensure WeChat is running and visible.")
        print("TIP: If WeChat is running as Administrator, you MUST run this terminal as Administrator too.")
        return False

    wechat_window.SetActive()
    wechat_window.SetTopmost(True)
    time.sleep(0.5)
    wechat_window.SetTopmost(False)

    print("Clicking search bar...")
    # Qt version often nests everything under a 'Weixin' Pane
    main_pane = wechat_window.PaneControl(Name="Weixin")
    
    # Try finding the search bar with more depth
    search_bar = wechat_window.EditControl(Name="搜索", searchDepth=3)
    if not search_bar.Exists(0, 0):
        search_bar = wechat_window.ButtonControl(Name="搜索", searchDepth=3)
    if not search_bar.Exists(0, 0) and main_pane.Exists(1, 0):
        search_bar = main_pane.EditControl(Name="搜索", searchDepth=2)
    
    if search_bar.Exists(2, 1):
        search_bar.Click()
        time.sleep(0.5)
        return True
    
    print("UI-based search bar not found. Attempting keyboard shortcut (Ctrl+F)...")
    wechat_window.SetActive()
    time.sleep(0.2)
    auto.SendKeys('{Ctrl}f')
    time.sleep(0.5)
    return True # Assume it worked if no error

def search_applet_only(applet_name):
    """Types the applet name into the search bar and submits, ensuring the bar is clear."""
    print(f"Typing search query: {applet_name}")
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(1, 0):
        wechat_window = auto.WindowControl(Name="微信")
    
    wechat_window.SetActive()
    time.sleep(0.5)

    # User Rule: Aggressively clear the search bar to avoid corruption
    # Method 1: Repeated Ctrl+A and Delete
    for _ in range(2):
        auto.SendKeys('{Ctrl}a')
        time.sleep(0.1)
        auto.SendKeys('{Delete}')
        time.sleep(0.1)
    
    # Method 2: Backspace a few times just in case Ctrl+A failed
    for _ in range(15):
        auto.SendKeys('{Back}')
    
    time.sleep(0.2)
    auto.SendKeys(applet_name)
    time.sleep(0.2)
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
    if not focus_wechat_and_open_search():
        return None
        
    search_applet_only(applet_name)
    print("Waiting 5 seconds for results to load...")
    time.sleep(5)
    
    if click_xiaochengxu_button():
        print("Waiting for applet window to appear...")
        # Verify and wait for the applet window
        for i in range(10):
            for window in auto.GetRootControl().GetChildren():
                if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                    print(f"Applet window detected: '{window.Name}'")
                    return window
            time.sleep(1)
            
    print("Could not verify applet window opening.")
    return None

if __name__ == "__main__":
    search_and_open_applet("霸王茶姬小程序")
