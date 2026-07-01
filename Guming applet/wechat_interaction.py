import uiautomation as auto
import time
import os
import subprocess

def close_stale_search_windows():
    """Closes any stale WeChat search result windows to avoid coordination clashes."""
    print("Closing stale 'Chrome_WidgetWin_0' windows named '微信'...")
    for window in auto.GetRootControl().GetChildren():
        try:
            if window.ClassName == "Chrome_WidgetWin_0" and ("微信" in window.Name or not window.Name):
                print(f"Closing window: Name='{window.Name}', PID={window.ProcessId}")
                window.SetActive()
                time.sleep(0.5)
                auto.SendKeys('{Alt}{F4}')
                time.sleep(1)
        except Exception as e:
            print(f"Error closing stale window: {e}")

def focus_wechat_and_open_search():
    """Focuses the main WeChat window and opens the search bar."""
    print("Looking for WeChat main window...")
    wechat_window = None
    try:
        wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon", searchDepth=1)
        if not wechat_window.Exists(1, 0):
            wechat_window = auto.WindowControl(Name="微信", searchDepth=1)
    except Exception as e:
        print(f"Error initializing window control: {e}")

    if not wechat_window or not wechat_window.Exists(3, 1):
        print("WeChat main window not found. Please ensure WeChat is running.")
        return False

    wechat_window.SetActive()
    wechat_window.SetTopmost(True)
    time.sleep(0.5)
    wechat_window.SetTopmost(False)

    print("Clicking search bar...")
    search_bar = wechat_window.EditControl(Name="搜索", searchDepth=3)
    if not search_bar.Exists(0, 0):
        search_bar = wechat_window.ButtonControl(Name="搜索", searchDepth=3)
    
    if search_bar.Exists(2, 1):
        search_bar.Click()
        time.sleep(0.5)
        return True
    
    print("UI-based search bar not found. Attempting Ctrl+F shortcut...")
    wechat_window.SetActive()
    time.sleep(0.2)
    auto.SendKeys('{Ctrl}f')
    time.sleep(0.5)
    return True

def search_applet_only(applet_name):
    """Clears search input and types the query."""
    print(f"Typing search query: {applet_name}")
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(1, 0):
        wechat_window = auto.WindowControl(Name="微信")
    
    wechat_window.SetActive()
    time.sleep(0.5)

    # Aggressively clear search bar
    for _ in range(2):
        auto.SendKeys('{Ctrl}a')
        time.sleep(0.1)
        auto.SendKeys('{Delete}')
        time.sleep(0.1)
    
    for _ in range(15):
        auto.SendKeys('{Back}')
    
    time.sleep(0.2)
    auto.SendKeys(applet_name)
    time.sleep(0.2)
    print("Search query typed.")

def find_search_result_window():
    """Finds and returns the search result window."""
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and ("微信" in window.Name or not window.Name):
            return window
    return None

def click_xiaochengxu_button(window):
    """Clicks the applet link in search results."""
    if not window:
        return False
    rect = window.BoundingRectangle
    target_x = rect.left + 151
    target_y = rect.top + 246
    print(f"Clicking Guming applet in search page at absolute coordinates: ({target_x}, {target_y})")
    window.SetActive()
    time.sleep(0.5)
    auto.Click(target_x, target_y)
    return True

def search_and_open_applet(applet_name="古茗"):
    """Launch Guming applet from scratch."""
    # 0. Force-terminate any stale applets at startup to clear state cache
    try:
        from cleanup_manager import close_guming_windows
        close_guming_windows()
    except Exception as e:
        print(f"Warning: Startup cleanup failed: {e}")
        
    # 1. Close stale windows
    close_stale_search_windows()
    
    # 2. Open search bar
    if not focus_wechat_and_open_search():
        return None
        
    # 3. Type search keyword
    search_applet_only(applet_name)
    time.sleep(2)
    
    # Get main WeChat window bounds
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(1, 0):
        wechat_window = auto.WindowControl(Name="微信")
        
    if not wechat_window.Exists(1, 0):
        print("WeChat main window not found.")
        return None
        
    rect = wechat_window.BoundingRectangle
    
    # Click '搜索网络结果' (Search Web Results) - Y is 135 relative to search bar top
    click_x = rect.left + 150
    click_y = rect.top + 135
    print(f"Clicking suggestion '搜索网络结果' at absolute ({click_x}, {click_y})")
    auto.Click(click_x, click_y)
    print("Waiting 5s for search results window...")
    time.sleep(5)
    
    # 4. Locate search results window and click applet link
    search_win = find_search_result_window()
    if not search_win:
        print("Search results window not found.")
        return None
        
    # Save NativeWindowHandle to a handle text file for cleanup purposes
    try:
        with open("search_window_handle.txt", "w") as f:
            f.write(str(search_win.NativeWindowHandle))
    except Exception as e:
        print(f"Warning: Could not save handle: {e}")
        
    if click_xiaochengxu_button(search_win):
        print("Waiting for Guming applet window to appear...")
        for i in range(12):
            for window in auto.GetRootControl().GetChildren():
                if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                    print(f"Guming applet window detected: '{window.Name}'")
                    return window
            time.sleep(1)
            
    print("Failed to open Guming applet window.")
    return None

if __name__ == "__main__":
    search_and_open_applet()
