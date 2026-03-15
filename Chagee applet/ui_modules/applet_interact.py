import uiautomation as auto
import time

def interact_with_applet(applet_name=None):
    """
    Sequence:
    1. Locate '小程序' window (Applet window)
    2. Click (118, 500)
    3. Wait 5s, click (63, 87)
    4. Wait 5s, move to (200, 566) and scroll down 200px
    """
    print("Starting applet interaction sequence...")
    
    # Locate the applet window
    # Applet windows usually have class 'Chrome_WidgetWin_0' and are NOT named '微信'
    applet_window = None
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                applet_window = window
                break
        if applet_window: break
        time.sleep(1)
        
    if not applet_window:
        print("Cannot find applet window for interaction.")
        return False
        
    applet_window.SetActive()
    rect = applet_window.BoundingRectangle
    print(f"Interacting with applet window '{applet_window.Name}' at {rect}")

    # 1. Click (118, 500)
    click1_x = rect.left + 118
    click1_y = rect.top + 500
    print(f"Clicking at relative (118, 500) -> Global ({click1_x}, {click1_y})")
    auto.Click(click1_x, click1_y)
    
    # 2. Wait 5s, click (63, 87)
    time.sleep(5)
    click2_x = rect.left + 63
    click2_y = rect.top + 87
    print(f"Clicking at relative (63, 87) -> Global ({click2_x}, {click2_y})")
    auto.Click(click2_x, click2_y)
    
    # 3. Wait 5s, scroll down starting at (200, 566)
    time.sleep(5)
    scroll_start_x = rect.left + 200
    scroll_start_y = rect.top + 566
    
    print(f"Scrolling down: Hovering at ({scroll_start_x}, {scroll_start_y}) and scrolling wheel...")
    # Move to the position without clicking to avoid triggering unwanted actions
    auto.MoveTo(scroll_start_x, scroll_start_y)
    time.sleep(0.5)
    
    # WheelDown simulates the mouse wheel. Increase to 10 times to ensure ~200px distance.
    auto.WheelDown(wheelTimes=2, interval=0.1)
    
    print("Interaction sequence complete.")
    return True

if __name__ == "__main__":
    interact_with_applet("Your Applet Name Here")
