"""
check_trigger.py
Function: Diagnostic script used to check if the applet parsing is working as expected.
It verifies if the target component (e.g. search bar or city title) can be found in the UI.
It then captures a screenshot of the applet window named 'debug_trigger.png' and highlights
both the assumed clicking coordinate (red circle) and the expected absolute coordinate
e.g. (49, 126) (green circle).

Run this if the scraper fails to interact with elements (like city switching) to visually inspect offsets.
"""

import uiautomation as auto
import cv2
import time
import os
import config

def get_applet_window():
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                return window
        time.sleep(1)
    return None

def main():
    applet = get_applet_window()
    if not applet:
        print("Applet window not found.")
        return
        
    applet.SetActive()
    time.sleep(1)
    
    print(f"Searching for trigger keyword: '{config.CITY_TRIGGER_KEYWORD}'")
    
    search_bar = applet.EditControl(Name=config.CITY_TRIGGER_KEYWORD, searchDepth=6)
    if not search_bar.Exists(5, 1):
        for edit in applet.GetChildren():
             if config.CITY_TRIGGER_KEYWORD in edit.Name:
                 search_bar = edit
                 break

    target_found = search_bar.Exists(1, 0)
    
    screenshot_path = "debug_trigger.png"
    auto.GetRootControl().CaptureToImage(screenshot_path)
    print(f"Screenshot saved to {screenshot_path}")
    img = cv2.imread(screenshot_path)
    
    if target_found:
        print(f"Successfully found trigger keyword control!")
        rect = search_bar.BoundingRectangle
        trigger_x = rect.left + config.CITY_TRIGGER_OFFSET_X
        trigger_y = rect.top + getattr(config, 'CITY_TRIGGER_OFFSET_Y', 0)
        
        center_red = (int(trigger_x), int(trigger_y))
        cv2.circle(img, center_red, 20, (0, 0, 255), 3)
        cv2.putText(img, "Clicking Spot", (center_red[0] + 25, center_red[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        print(f"Circled clicking spot at {center_red} in red.")
    else:
        print(f"Failed to find '{config.CITY_TRIGGER_KEYWORD}'.")
        print("Will try to find ANY EditControl or '上海' to guess the clicking spot.")
        # Try to find the first edit control
        edit_control = applet.EditControl(searchDepth=6)
        if edit_control.Exists(1, 0):
            print(f"Found an EditControl with Name: '{edit_control.Name}'. Will use this as fallback.")
            rect = edit_control.BoundingRectangle
            trigger_x = rect.left + config.CITY_TRIGGER_OFFSET_X
            trigger_y = rect.top + getattr(config, 'CITY_TRIGGER_OFFSET_Y', 0)
            center_red = (int(trigger_x), int(trigger_y))
            cv2.circle(img, center_red, 20, (0, 0, 255), 3)
            cv2.putText(img, "Fallback Clicking Spot", (center_red[0] + 25, center_red[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        else:
            # Maybe the city name "上海"?
            city_btn = applet.TextControl(Name="上海", searchDepth=6)
            if city_btn.Exists(1, 0):
                print("Found '上海' TextControl, using its center as clicking spot.")
                rect = city_btn.BoundingRectangle
                center_red = (int((rect.left + rect.right)/2), int((rect.top + rect.bottom)/2))
                cv2.circle(img, center_red, 20, (0, 0, 255), 3)
                cv2.putText(img, "City '上海'", (center_red[0] + 25, center_red[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            else:
                print("Could not find any EditControl or '上海'. Cannot draw red circle.")
    
    # Relative to applet window:
    applet_rect = applet.BoundingRectangle
    green_x = applet_rect.left + 49
    green_y = applet_rect.top + 126
    center_green = (int(green_x), int(green_y))
    cv2.circle(img, center_green, 20, (0, 255, 0), 3)
    cv2.putText(img, "Relative (49,126)", (center_green[0] + 25, center_green[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    print(f"Circled applet-relative (49, 126) which is absolute {center_green} in green.")
    
    cv2.imwrite(screenshot_path, img)
    print(f"Updated screenshot saved to {screenshot_path}")

if __name__ == "__main__":
    main()
