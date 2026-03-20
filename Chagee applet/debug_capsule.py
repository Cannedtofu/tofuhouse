import uiautomation as auto
import time

def find_capsule():
    for window in auto.GetRootControl().GetChildren():
        if "霸王茶姬" in window.Name or (window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != ""):
            print(f"Found Window: '{window.Name}'")
            window.SetActive()
            window.CaptureToImage("d:\\代码项目\\Chagee applet\\debug_capsule.png")
            rect = window.BoundingRectangle
            print(f"Window Rect: {rect.left}, {rect.top}, {rect.right}, {rect.bottom}")
            print(f"Proposed click at: ({rect.right - 40}, {rect.top + 30})")
            # Let's try to find if there are any specific controls for the capsule
            # Usually there aren't for applets, but let's check
            break

if __name__ == "__main__":
    find_capsule()
