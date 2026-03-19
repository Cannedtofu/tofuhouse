import uiautomation as auto
import time
import ctypes

def get_window_rect(window):
    rect = window.BoundingRectangle
    return rect.left, rect.top, rect.right, rect.bottom

def try_close_by_click(window_name="霸王茶姬"):
    print(f"Searching for '{window_name}'...")
    window = None
    for w in auto.GetRootControl().GetChildren():
        if window_name in w.Name:
            window = w
            break
    
    if not window:
        print("Window not found.")
        return False

    window.SetActive()
    time.sleep(1)
    
    left, top, right, bottom = get_window_rect(window)
    width = right - left
    height = bottom - top
    print(f"Window Rect: {left}, {top}, {right}, {bottom} (W={width}, H={height})")

    # WeChat applets have a close button roughly at the top right.
    # The 'O' button in the capsule.
    # Usually it's about 30-50 pixels from the right and 20-30 pixels from the top.
    # Let's try 40px from right, 30px from top.
    click_x = right - 40
    click_y = top + 30
    
    print(f"Attempting to click close button at ({click_x}, {click_y})...")
    auto.Click(click_x, click_y)
    
    time.sleep(2)
    # Check if window still exists
    for w in auto.GetRootControl().GetChildren():
        if window_name in w.Name:
            print("Window still exists after click.")
            return False
    
    print("Success: Window closed by click.")
    return True

def try_close_by_alt_f4(window_name="霸王茶姬"):
    print(f"Attempting Alt+F4 on '{window_name}'...")
    window = None
    for w in auto.GetRootControl().GetChildren():
        if window_name in w.Name:
            window = w
            break
    
    if not window:
        print("Window not found.")
        return True # Considered closed if not found
    
    window.SetActive()
    time.sleep(0.5)
    auto.SendKeys('{Alt}{F4}')
    time.sleep(2)
    
    for w in auto.GetRootControl().GetChildren():
        if window_name in w.Name:
            print("Window still exists after Alt+F4.")
            return False
            
    print("Success: Window closed by Alt+F4.")
    return True

if __name__ == "__main__":
    if not try_close_by_click():
        if not try_close_by_alt_f4():
            print("Both methods failed.")
        else:
            print("Alt+F4 worked.")
    else:
        print("Click worked.")
