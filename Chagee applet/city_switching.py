import uiautomation as auto
import time
import os
import sys
import cv2
import numpy as np
import pypinyin
import config

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
    # Method: Use OCR to find keyword from config and shift
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
    target_keyword = config.CITY_TRIGGER_KEYWORD
    
    for box, (text, score) in full_res:
        if target_keyword in text:
            cx = (box[0][0] + box[1][0]) / 2
            cy = (box[0][1] + box[2][1]) / 2
            
            rel_x = cx + config.CITY_TRIGGER_OFFSET_X
            rel_y = cy
            
            trigger_x = int(rect.left + rel_x)
            trigger_y = int(rect.top + rel_y)
            print(f"Found '{target_keyword}' at ({cx}, {cy}). Shifting to click ({rel_x}, {rel_y})")
            
            # Debug: Mark and crop
            img = load_img(screenshot_path)
            if img is not None:
                cv2.rectangle(img, (int(rel_x)-2, int(rel_y)-2), (int(rel_x)+2, int(rel_y)+2), (0, 0, 255), -1)
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

    # 2. Find index character (Pinyin Initial)
    pinyin_result = pypinyin.pinyin(city_name, style=pypinyin.Style.FIRST_LETTER)
    initial = pinyin_result[0][0].upper()
    print(f"City '{city_name}' starts with '{initial}'")

    idx_region = config.CITY_INDEX_REGION
    
    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "city_index_full.png")
    applet_window.CaptureToImage(screenshot_path)
    full_res = extractor.ocr_full_image(screenshot_path)
    
    found_initial = False
    for box, (text, score) in full_res:
        if initial == text.strip().upper():
            cx = (box[0][0] + box[1][0]) / 2
            cy = (box[0][1] + box[2][1]) / 2
            
            if idx_region['x_min'] <= cx <= idx_region['x_max'] and \
               idx_region['y_min'] <= cy <= idx_region['y_max']:
                print(f"Found index '{initial}' at ({cx}, {cy}). Clicking...")
                auto.Click(int(rect.left + cx), int(rect.top + cy))
                found_initial = True
                time.sleep(2)
                break
    
    if not found_initial:
        print(f"Could not find index character '{initial}' in specified region.")

    # 3. Search for city name in list
    city_found = False
    for attempt in range(1, 11):
        scan_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "city_list_scan.png")
        applet_window.CaptureToImage(scan_path)
        cities_res = extractor.ocr_full_image(scan_path)
        
        for box, (text, score) in cities_res:
            if city_name in text:
                cx = (box[0][0] + box[1][0]) / 2
                cy = (box[0][1] + box[2][1]) / 2
                
                print(f"Found city '{city_name}' at ({cx}, {cy}). Clicking...")
                auto.Click(int(rect.left + cx), int(rect.top + cy))
                city_found = True
                time.sleep(5)
                break
        
        if city_found:
            break
            
        print(f"City '{city_name}' not visible. Scrolling down (Attempt {attempt}/10)...")
        auto.MoveTo(rect.left + 200, rect.top + 500)
        auto.WheelDown(wheelTimes=config.SCROLL_WHEEL_TIMES, interval=0.1)
        time.sleep(2)
            
    if city_found:
        print(f"Successfully switched to {city_name}.")
        return True
    else:
        print(f"Failed to find city '{city_name}' after 10 scrolls.")
        return False

if __name__ == "__main__":
    from config import CITY_LIST
    for city in CITY_LIST:
        switch_city(city[0], city[1], city[2])
        time.sleep(2)
