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


if __name__ == "__main__":
    focus_wechat_and_open_search()
