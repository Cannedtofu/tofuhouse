import uiautomation as auto
import time
import os
import sys
import cv2
import numpy as np
from pypinyin import lazy_pinyin
from config import CITY_LIST

# Add parent directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from ocr_extractor import ChageeOCRExtractor

def switch_city(city_name, target_store_count, initial_location):
    print(f"Switching to city: {city_name}")
    extractor = ChageeOCRExtractor()
    
    # 1. Find Applet Window
    applet_window = None
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                applet_window = window
                break
        if applet_window: break
        time.sleep(1)
        
    if not applet_window:
        print("Cannot find applet window.")
        return False
        
    applet_window.SetActive()
    rect = applet_window.BoundingRectangle
    
    # 1. Click Select City button
    # Method: Use OCR to find "搜索门店" and shift left by 89 pixels
    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "city_trigger_debug.png")
    applet_window.CaptureToImage(screenshot_path)
    
    def load_img(path):
        try:
            with open(path, 'rb') as f:
                return cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
        except: return None

    def save_img(path, img):
        try:
            _, ext = os.path.splitext(path)
            res, encode = cv2.imencode(ext, img)
            if res:
                with open(path, 'wb') as f: f.write(encode)
        except: pass

    full_res = extractor.ocr_full_image(screenshot_path)
    trigger_x, trigger_y = None, None
    
    # Target keyword provided by USER
    target_keyword = "搜索门店"
    
    for box, (text, score) in full_res:
        if target_keyword in text:
            # Center of "搜索门店"
            cx = (box[0][0] + box[1][0]) / 2
            cy = (box[0][1] + box[2][1]) / 2
            
            # User Rule: Shift left by 89 pixels
            rel_x = cx - 89
            rel_y = cy
            
            trigger_x = int(rect.left + rel_x)
            trigger_y = int(rect.top + rel_y)
            print(f"Found '{target_keyword}' at ({cx}, {cy}). Shifting left 89px to ({rel_x}, {rel_y})")
            
            # Debug: Mark and crop
            img = load_img(screenshot_path)
            if img is not None:
                # Mark click spot: Red rectangle 4x4
                cv2.rectangle(img, (int(rel_x)-2, int(rel_y)-2), (int(rel_x)+2, int(rel_y)+2), (0, 0, 255), -1)
                
                # Crop area around calculated point
                x1, x2 = max(0, int(rel_x)-50), min(img.shape[1], int(rel_x)+50)
                y1, y2 = max(0, int(rel_y)-50), min(img.shape[0], int(rel_y)+50)
                debug_crop = img[y1:y2, x1:x2]
                
                save_img(os.path.join(os.path.dirname(os.path.abspath(__file__)), "city_trigger_crop.png"), debug_crop)
                save_img(screenshot_path, img)
            break
            
    if trigger_x is None:
        print(f"Could not find '{target_keyword}' for city selection trigger. Aborting.")
        return False

    auto.Click(trigger_x, trigger_y)
    time.sleep(5)
    
    # 3. Calculate first Pinyin character
    first_char = lazy_pinyin(city_name)[0][0].upper()
    print(f"City '{city_name}' starts with '{first_char}'")
    
    # 4. Locate index character in region (386, 386) to (406, 807)
    # We take a screenshot of the window and crop this region
    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "city_index_full.png")
    applet_window.CaptureToImage(screenshot_path)
    
    # Coordinates are relative to applet window? 
    # USER says (386, 386) to (406, 807). I'll assume these are window-relative.
    # We'll use full OCR and filter by coordinates
    full_res = extractor.ocr_full_image(screenshot_path)
    
    index_click_pos = None
    for box, (text, score) in full_res:
        # box is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        text_center_x = (box[0][0] + box[1][0]) / 2
        text_center_y = (box[0][1] + box[2][1]) / 2
        
        # Check if text is the character we want and in the side region
        if text.upper() == first_char:
            if 380 <= text_center_x <= 410 and 380 <= text_center_y <= 810:
                index_click_pos = (int(rect.left + text_center_x), int(rect.top + text_center_y))
                break
                
    if index_click_pos:
        print(f"Found index '{first_char}' at {index_click_pos}. Clicking...")
        auto.Click(index_click_pos[0], index_click_pos[1])
        time.sleep(2)
    else:
        print(f"Could not find index character '{first_char}' in specified region.")
        # We'll continue anyway and try to find the city directly
        
    # 5. Find city name and click
    city_found = False
    for scroll_retry in range(11): # 0 to 10
        # Capture again
        applet_window.CaptureToImage(screenshot_path)
        full_res = extractor.ocr_full_image(screenshot_path)
        
        for box, (text, score) in full_res:
            if city_name in text:
                city_center_x = (box[0][0] + box[1][0]) / 2
                city_center_y = (box[0][1] + box[2][1]) / 2
                click_x = int(rect.left + city_center_x)
                click_y = int(rect.top + city_center_y)
                
                print(f"Found city '{city_name}' at ({click_x}, {click_y}). Clicking...")
                auto.Click(click_x, click_y)
                city_found = True
                time.sleep(5)
                auto.WheelUp(wheelTimes=1, interval=0.1)
                
                break
        
        if city_found:
            break
            
        if scroll_retry < 10:
            print(f"City '{city_name}' not visible. Scrolling down (Attempt {scroll_retry+1}/10)...")
            # Scroll in the main city list area
            scroll_start_x = rect.left + 200
            scroll_start_y = rect.top + 500
            auto.MoveTo(scroll_start_x, scroll_start_y)
            auto.WheelDown(wheelTimes=3, interval=0.1)
            time.sleep(2)
        else:
            print(f"Failed to find city '{city_name}' after 10 scrolls.")
            return False
            
    print(f"Successfully switched to {city_name}.")
    return True

if __name__ == "__main__":
    for city in CITY_LIST:
        success = switch_city(city[0], city[1], city[2])
        if not success:
            print(f"Adding {city[0]} to failed list.")
        time.sleep(2)
