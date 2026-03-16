import uiautomation as auto
import time

def inspect_window():
    print("Searching for Applet Window...")
    applet_window = None
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
            applet_window = window
            break
            
    if not applet_window:
        print("Window not found.")
        return

    applet_window.SetActive()
    rect = applet_window.BoundingRectangle
    print(f"Window Name: {applet_window.Name}")
    print(f"Window Rect: {rect}")
    
    # Take a screenshot to see what's at (50, 396)
    import os
    screenshot_path = os.path.join(os.getcwd(), "inspect_city.png")
    applet_window.CaptureToImage(screenshot_path)
    print(f"Screenshot saved to {screenshot_path}")

    # Inspect children (deeply if needed)
    print("\n--- Child Elements ---")
    def walk(control, depth=0):
        if depth > 2: return
        for child in control.GetChildren():
            print("  " * depth + f"- Name: {child.Name}, Class: {child.ClassName}, Rect: {child.BoundingRectangle}")
            walk(child, depth + 1)
    
    walk(applet_window)

if __name__ == "__main__":
    inspect_window()
