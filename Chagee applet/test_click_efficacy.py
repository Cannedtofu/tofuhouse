import scraping_logic
import uiautomation as auto
import time
import config

def test_click():
    applet = scraping_logic.get_applet_window()
    if not applet:
        print("Applet not found")
        return
        
    applet.SetActive()
    time.sleep(1)
    
    rect = applet.BoundingRectangle
    tx = rect.left + config.CITY_TRIGGER_COORD[0]
    ty = rect.top + config.CITY_TRIGGER_COORD[1]
    
    print(f"Clicking coordinate: {tx}, {ty}")
    auto.Click(tx, ty)
    
    print("Waiting 3 seconds for UI to update...")
    time.sleep(3)
    
    screenshot_path = "debug_after_click.png"
    auto.GetRootControl().CaptureToImage(screenshot_path)
    print(f"Saved screenshot of the result to {screenshot_path}")
    
    # Try finding '杭州'
    city_target = auto.TextControl(Name="杭州", searchDepth=8)
    if city_target.Exists(1, 0):
        print("SUCCESS! Found '杭州' in the new view.")
    else:
        print("FAILED: Could not find '杭州' in the view after click.")
        
if __name__ == "__main__":
    test_click()
