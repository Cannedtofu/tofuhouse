import uiautomation as auto
import time
import os
import sys
import cv2
import numpy as np
import pandas as pd
from datetime import datetime
from paddleocr import PaddleOCR
import json
from pypinyin import pinyin, Style

# Import configuration
import config
from config import CITY_LIST

# --- Group 2: Chagee Applet Navigation and Main Scraping Logic ---

class ChageeOCRExtractor:
    def __init__(self, lang='ch'):
        self.lang = lang
        # Initialize PaddleOCR
        # use_angle_cls=True helps with slightly rotated text
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def _load_image(self, path):
        with open(path, 'rb') as f:
            img_array = np.frombuffer(f.read(), np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    def _preprocess_for_ocr(self, img):
        # Upscale
        img = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        # PaddleOCR 3.4+ prefers BGR or RGB (3 channels)
        return img

    def extract_data(self, image_path):
        img = self._load_image(image_path)
        if img is None:
            return []

        h_full, w_full = img.shape[:2]
        # Ignore top static elements (search/map)
        roi_y = int(h_full * config.ROI_TOP_IGNORE_PERCENT)
        roi = img[roi_y:, :]
        
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # More sensitive edges
        edges = cv2.Canny(gray, 20, 100)
        kernel = np.ones((3, 3), np.uint8)
        # Reduce dilation iterations to avoid merging adjacent boxes
        dilated = cv2.dilate(edges, kernel, iterations=1) 
        
        # Use RETR_LIST to catch all contours including internal ones
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Slightly relaxed constraints
            if config.BOX_MIN_WIDTH <= w <= config.BOX_MAX_WIDTH and \
               config.BOX_MIN_HEIGHT <= h <= config.BOX_MAX_HEIGHT:
                # Store (area, box) to help deduplication prefer smaller boxes
                boxes.append((w * h, (x, y + roi_y, w, h)))
        
        # Sort by area ascending so we process smaller boxes first in deduplication
        boxes.sort(key=lambda x: x[0])
        
        final_boxes = []
        for area, b in boxes:
            is_overlap = False
            b_y1, b_y2 = b[1], b[1] + b[3]
            for fb in final_boxes:
                fb_y1, fb_y2 = fb[1], fb[1] + fb[3]
                # Calculate y-overlap
                overlap = max(0, min(b_y2, fb_y2) - max(b_y1, fb_y1))
                if overlap > 0:
                    # If overlap is significant (> 50% of the smaller box)
                    smaller_h = min(b[3], fb[3])
                    if overlap / smaller_h > 0.5:
                        is_overlap = True
                        break
            if not is_overlap:
                final_boxes.append(b)
        
        # Finally sort by Y for top-to-bottom processing
        final_boxes.sort(key=lambda b: b[1])
        
        print(f"Detected {len(final_boxes)} boxes in {os.path.basename(image_path)}")
        
        extracted_data = []
        debug_base = os.path.join(os.path.dirname(image_path), f"debug_{os.path.basename(image_path)}")
        if not os.path.exists(debug_base):
            os.makedirs(debug_base)

        for i, (bx, by, bw, bh) in enumerate(final_boxes):
            box_img = img[by:by + bh, bx:bx + bw]
            self._save_image(os.path.join(debug_base, f"box_{i}_full.png"), box_img)
            
            # User Rule: finalized cropping logic
            sn_y_start = config.SN_CROP_Y_START 
            sn_height = config.SN_CROP_HEIGHT 
            # Out of bounds safety for partial/bottom boxes
            y1, y2 = min(sn_y_start, bh-1), min(sn_y_start + sn_height, bh)
            sn_roi = box_img[y1:y2, 10 : int(bw * config.BOX_WIDTH_CUTOFF_PERCENT)]
            if sn_roi.size == 0: continue
            
            # User Rule: Order status crop offset
            os_y_start = config.OS_CROP_Y_START
            os_height = config.OS_CROP_HEIGHT 
            y3, y4 = min(os_y_start, bh-1), min(os_y_start + os_height, bh)
            os_roi = box_img[y3:y4, 10 : int(bw * config.BOX_WIDTH_CUTOFF_PERCENT)]
            if os_roi.size == 0: os_roi = np.zeros((1, 1, 3), dtype=np.uint8) # Dummy for OCR skip
            
            # Log for inspection as requested
            self._save_image(os.path.join(debug_base, f"box_{i}_sn_crop.png"), sn_roi)
            self._save_image(os.path.join(debug_base, f"box_{i}_os_crop.png"), os_roi)
            
            # OCR part using PaddleOCR
            sn_proc = self._preprocess_for_ocr(sn_roi)
            os_proc = self._preprocess_for_ocr(os_roi)
            
            # Use rec only (det=False) because we already cropped the ROI
            # Format: [[(text, score)]]
            sn_res = self.ocr.ocr(sn_proc, det=False, cls=True)
            store_name_raw = sn_res[0][0][0] if sn_res and sn_res[0] else ""
            
            os_res = self.ocr.ocr(os_proc, det=False, cls=True)
            order_status_raw = os_res[0][0][0] if os_res and os_res[0] else ""
            
            def clean_text(text):
                # Aggressive cleaning for store names: mostly Chinese
                text = "".join([c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum()])
                replacements = {
                    '卜消': '上海', '一浪': '上海', '一津': '上海', '广海': '上海', 
                    '门海': '上海', '一街': '上海', '卜海': '上海', '门浴': '上海',
                    '卜浴': '上海', '一浪': '上海', '卜淡': '上海', '户海': '上海',
                    '喜号': '壹号', '金英': '金茂', '志': '店', '庆': '店', '交': '店', '底': '店',
                    '东一明珠': '东方明珠', '漫园': '漫圈', '芸': '荟'
                }
                for k, v in replacements.items():
                    text = text.replace(k, v)
                
                # If it doesn't start with '上海', but looks like it might
                if len(text) > 2 and text[0] in '广门卜一' and text[1] in '海浴淡浪':
                    text = '上海' + text[2:]
                
                # Suffix fix: if ends in thing that's likely '店'
                if len(text) > 4 and text[-1] in '志庆交底不面銀適影':
                    text = text[:-1] + '店'
                
                # If '店' is second to last, remove the last character (likely OCR noise)
                if len(text) >= 2 and text[-2] == '店':
                    text = text[:-1]

                return text.strip()

            store_name = clean_text(store_name_raw)
            
            # Strict Filtering to remove non-store boxes
            if not ('上海' in store_name or '店' in store_name):
                continue
            if len(store_name) < 4:
                continue
            if any(k in store_name for k in ['外卖', '到店', '自取', '筛选', '搜索']):
                continue
                
            def parse_status(raw):
                # Status-specific misrecognitions: '林' often read as '杯'
                normalized = raw.replace('卵', '即').replace('刻', '制').replace('佛', '作').replace('下羡', '下单').replace('析', '6').replace('怀', '坏').replace('广单', '下单').replace('林', '杯')
                
                import re
                digits = re.findall(r'\d+', normalized)
                cup_count = 0
                
                # Rule: Strict format check for "前方x杯制作中"
                # Must have digits, '杯', and either '前方' or '制作'
                if digits and '杯' in normalized and (any(k in normalized for k in ['前方', '制作', '中', '制'])):
                    cup_count = int(digits[0])
                    return f"前方{cup_count}杯制作中", cup_count
                
                # Rule: Check for "现在下单，立即制作"
                if any(k in normalized for k in ['立即制作', '现在下单', '立即', '下单', '制作中']):
                    return "现在下单，立即制作", 0
                
                # If it doesn't match the primary patterns, avoid returning random digits
                # Just return cleaned text but set count to 0
                cleaned = clean_text(normalized)
                return cleaned if cleaned else "已休息", 0

            order_status, cup_count = parse_status(order_status_raw)
            
            # --- New Feature: High Threshold Screenshot ---
            if getattr(config, 'SCREENSHOT_ON_THRESHOLD', False) and cup_count >= getattr(config, 'CUP_COUNT_THRESHOLD', 80):
                try:
                    from datetime import datetime
                    project_dir = os.path.dirname(os.path.abspath(__file__))
                    data_dir = os.path.join(project_dir, getattr(config, 'DATA_FOLDER', 'data'))
                    if not os.path.exists(data_dir):
                        os.makedirs(data_dir)
                    
                    # Clean filename (remove special chars from store name)
                    clean_name = "".join([c for c in store_name if c.isalnum() or c in (" ", "-", "_")])
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{clean_name}_{cup_count}cups_{timestamp}.png"
                    save_path = os.path.join(data_dir, filename)
                    
                    # Save the full box image for context
                    self._save_image(save_path, box_img)
                    print(f"  [!] Threshold hit: Saved screenshot for {store_name} ({cup_count} cups) to {filename}")
                except Exception as e:
                    print(f"  [!] Failed to save threshold screenshot: {e}")

            extracted_data.append({
                "store_name": store_name,
                "order_status": order_status,
                "cup_count": cup_count,
                "debug_sn": os.path.join(debug_base, f"box_{i}_sn_crop.png"),
                "debug_os": os.path.join(debug_base, f"box_{i}_os_crop.png")
            })

        
        return extracted_data

    def ocr_full_image(self, image_path):
        img = self._load_image(image_path)
        if img is None:
            return []
        # Full OCR with detection
        res = self.ocr.ocr(img, det=True, cls=True)
        return res[0] if res else []

    def _save_image(self, path, img):
        _, ext = os.path.splitext(path)
        res, img_encode = cv2.imencode(ext, img)
        if res:
            with open(path, 'wb') as f:
                f.write(img_encode)

def switch_city(applet_window, city_name, target_count, current_city=None, extractor=None):
    """
    Switching city involves:
    1. OCR '搜索门店' and click left offset.
    2. Getting the Pinyin initial, and OCR-ing finding it on the sidebar box.
    3. Scrolling and finding the city by text.
    """
    print(f"\n--- Initiating Switch to {city_name} ---")
    
    rect = applet_window.BoundingRectangle
    trigger_x, trigger_y = -1, -1
    
    project_dir = os.path.dirname(os.path.abspath(__file__))
    temp_img = os.path.join(project_dir, "temp_search_trigger.png")
    
    if extractor:
        # 1. OCR finding for "搜索门店"
        applet_window.CaptureToImage(temp_img)
        results = extractor.ocr_full_image(temp_img)
        keyword = getattr(config, 'CITY_TRIGGER_KEYWORD', '搜索门店')
        
        for res in results:
            box, (text, score) = res
            if keyword in text:
                center_x = (box[0][0] + box[2][0]) / 2.0
                center_y = (box[0][1] + box[2][1]) / 2.0
                trigger_x = int(rect.left + center_x + getattr(config, 'CITY_TRIGGER_OFFSET_X', -89))
                trigger_y = int(rect.top + center_y + getattr(config, 'CITY_TRIGGER_OFFSET_Y', 0))
                print(f"OCR found '{text}' at relative ({center_x}, {center_y}). Trigger global coordinate: ({trigger_x}, {trigger_y})")
                break
                
    if trigger_x == -1:
        print(f"OCR failed to find '{getattr(config, 'CITY_TRIGGER_KEYWORD', '搜索门店')}'. Using direct coordinate fallback.")
        # Fallback to older keyword search approach
        keyword = getattr(config, 'CITY_TRIGGER_KEYWORD', '搜索门店')
        search_bar = applet_window.EditControl(Name=keyword, searchDepth=6)
        if not search_bar.Exists(5, 1):
            for edit in applet_window.GetChildren():
                 if keyword in edit.Name:
                     search_bar = edit
                     break

        if not search_bar.Exists(1, 0):
            print(f"Could not find trigger button to switch city.")
            return False
        
        s_rect = search_bar.BoundingRectangle
        trigger_x = s_rect.left + getattr(config, 'CITY_TRIGGER_OFFSET_X', -89)
        trigger_y = s_rect.top + getattr(config, 'CITY_TRIGGER_OFFSET_Y', 0)
    
    auto.Click(trigger_x, trigger_y)
    time.sleep(2)

    # 2. Get city initial for index navigation
    initial = pinyin(city_name, style=Style.FIRST_LETTER)[0][0].upper()
    print(f"Navigating to index '{initial}' for city '{city_name}'...")
    
    clicked_index = False
    if extractor:
        # Re-capture the window as UI has changed after clicking to the city selector page
        applet_window.CaptureToImage(temp_img)
        
        # Use paddleocr to search for initial in the whole applet screen to avoid OpenCV unicode path issues
        idx_results = extractor.ocr_full_image(temp_img)
        for res in idx_results:
            box, (text, score) = res
            if initial in text.upper():
                center_x_local = (box[0][0] + box[2][0]) / 2.0
                center_y_local = (box[0][1] + box[2][1]) / 2.0
                
                # Check if the initial found is actually structurally in the sidebar layout region 
                # (Relaxed box around 387,213 and 405,632)
                if 350 <= center_x_local <= 420 and 200 <= center_y_local <= 650:
                    target_click_x = int(rect.left + center_x_local)
                    target_click_y = int(rect.top + center_y_local)
                    print(f"OCR found initial '{initial}' in sidebar box. Clicking absolute: ({target_click_x}, {target_click_y})")
                    auto.Click(target_click_x, target_click_y)
                    clicked_index = True
                    time.sleep(1)
                    break
                    
    if not clicked_index:
        print(f"OCR failed to find initial '{initial}', attempting manual TextControl UI search...")
        index_btn = auto.TextControl(Name=initial, searchDepth=8)
        if index_btn.Exists(2, 1):
            index_btn.Click()
            time.sleep(1)
        else:
            print(f"Index button '{initial}' not found in UI tree either, attempting manual scroll...")

    # 3. Find and click city name using OCR
    print(f"Searching for city '{city_name}' inside view...")
    
    def try_find_city_ocr():
        if not extractor:
            return False
            
        applet_window.CaptureToImage(temp_img)
        res_list = extractor.ocr_full_image(temp_img)
        for res in res_list:
            box, (text, score) = res
            # Strict match to avoid partials unless absolutely necessary, 
            # but usually WeChat lists just say "杭州" or "杭州市".
            if city_name in text:
                cx = (box[0][0] + box[2][0]) / 2.0
                cy = (box[0][1] + box[2][1]) / 2.0
                click_x = int(rect.left + cx)
                click_y = int(rect.top + cy)
                print(f"OCR found city '{text}'. Clicking absolute: ({click_x}, {click_y}).")
                auto.Click(click_x, click_y)
                time.sleep(3) # Wait for list to refresh
                return True
        return False

    if try_find_city_ocr():
        return True
        
    # Legacy fallback if no extractor
    city_target = None
    if not extractor:
        city_target = auto.TextControl(Name=city_name, searchDepth=8)
        if city_target.Exists(1, 0):
            city_target.Click()
            time.sleep(3)
            return True

    # If not found, scroll a bit in the city list area
    print(f"City '{city_name}' not in view, scrolling...")
    scroll_x = int((rect.left + rect.right) / 2)
    scroll_y = int((rect.top + rect.bottom) / 2)
    auto.MoveTo(scroll_x, scroll_y) # Hover over center of applet
    
    for _ in range(getattr(config, 'CITY_SCROLL_MAX_RETRIES', 5)):
        auto.WheelDown(wheelTimes=getattr(config, 'CITY_SCROLL_WHEEL_TIMES', 3), interval=0.1)
        time.sleep(1) # Give it an extra moment to render the new list before OCR
        
        if extractor:
            if try_find_city_ocr():
                return True
        else:
            if city_target and city_target.Exists(1, 0):
                city_target.Click()
                time.sleep(3)
                return True
            
    print(f"Failed to find city '{city_name}' in the list.")
    return False

def get_applet_window():
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                return window
        time.sleep(1)
    return None

def scrape_city_stores(applet_window, extractor, target_count=None, city_name="Default", click_entry=True):
    if target_count is None:
        target_count = config.DEFAULT_TARGET_COUNT
        
    print(f"\n--- Scraping City: {city_name} (Target: {target_count}) ---")
    applet_window.SetActive()
    time.sleep(1)
    rect = applet_window.BoundingRectangle
    
    if click_entry:
        # 1. Click entry coordinate leads to the scrolling page
        entry_x = rect.left + config.STORE_LIST_ENTRY_REL_COORD[0]
        entry_y = rect.top + config.STORE_LIST_ENTRY_REL_COORD[1]
        print(f"Clicking at relative {config.STORE_LIST_ENTRY_REL_COORD} -> Global ({entry_x}, {entry_y})")
        auto.Click(entry_x, entry_y)
        time.sleep(5)
    else:
        print(f"Skipping entry click {config.STORE_LIST_ENTRY_REL_COORD} as requested.")
    
    # 2. Initial scroll / Reset to Top
    scroll_x = rect.left + config.SCROLL_REL_COORD[0]
    scroll_y = rect.top + config.SCROLL_REL_COORD[1]
    auto.MoveTo(scroll_x, scroll_y)
    
    # User Rule: Force reset to top to avoid missing entries
    print("Resetting scroll to top of list...")
    auto.WheelUp(wheelTimes=10, interval=0.1)
    time.sleep(1)
    
    auto.WheelDown(wheelTimes=3, interval=0.1) # Initial settling
    time.sleep(2)

    city_results = {}
    consecutive_no_new = 0
    max_no_new_scrolls = config.MAX_NO_NEW_SCROLLS
    
    while len(city_results) < target_count and consecutive_no_new < max_no_new_scrolls:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        screenshot_path = os.path.join(project_dir, "temp_scrape.png")
        applet_window.CaptureToImage(screenshot_path)
        
        results = extractor.extract_data(screenshot_path)
        new_found = 0
        for res in results:
            name = res['store_name']
            if name not in city_results:
                res['City'] = city_name
                city_results[name] = res
                new_found += 1
                print(f"  [+] {city_name}: {name}")
        
        if new_found > 0:
            consecutive_no_new = 0
        else:
            consecutive_no_new += 1
            print(f"  [!] No new stores found ({consecutive_no_new}/{max_no_new_scrolls}).")
            
        if len(city_results) >= target_count: break
            
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelDown(wheelTimes=config.SCROLL_WHEEL_TIMES, interval=0.1)
        time.sleep(2)

    if consecutive_no_new >= max_no_new_scrolls:
        print(f"  [!] Aborted {city_name}: Reached limit of {max_no_new_scrolls} scrolls without new data.")

    print(f"Scraped {len(city_results)} stores in {city_name}.")
    return list(city_results.values())

def main_workflow():
    applet_window = get_applet_window()
    if not applet_window:
        print("Applet window not found.")
        return

    extractor = ChageeOCRExtractor()
    all_results = []

    # 1. Scrape the first city (implicitly current)
    initial_res = scrape_city_stores(applet_window, extractor, config.DEFAULT_TARGET_COUNT, "上海")
    now = datetime.now()
    for r in initial_res:
        r['Date'] = now.strftime("%Y-%m-%d")
        r['Time'] = now.strftime("%H:%M")
        r['Day'] = now.strftime("%A")
    all_results.extend(initial_res)

    # 2. Move on to rest of cities
    for city_name, target_count, _ in CITY_LIST:
        # Switch City directly 
        if switch_city(applet_window, city_name, target_count, None, extractor):
            # After switching, we are already on the store list page
            city_res = scrape_city_stores(applet_window, extractor, target_count, city_name, click_entry=False)
            now = datetime.now()
            for r in city_res:
                r['Date'] = now.strftime("%Y-%m-%d")
                r['Time'] = now.strftime("%H:%M")
                r['Day'] = now.strftime("%A")
            all_results.extend(city_res)
        else:
            print(f"Failed to switch to {city_name}. Skipping.")

    # Final Export
    if all_results:
        # Import data handler dynamically to resolve circular dependency if any
        # though ideally main.py calls this.
        try:
            from data_manager import save_results_to_excel
            save_results_to_excel(all_results)
        except ImportError:
            # Fallback if reorganization isn't complete
            export_data = []
            for r in all_results:
                export_data.append({
                    "City": r.get('City', 'Unknown'),
                    "Store Name": r['store_name'],
                    "Order Status": r['order_status'],
                    "Cup Count": r['cup_count'],
                    "Date": r.get('Date', ''),
                    "Time": r.get('Time', ''),
                    "Day": r.get('Day', '')
                })
                
            project_dir = os.path.dirname(os.path.abspath(__file__))
            output_file = os.path.join(project_dir, "multi_city_stores.xlsx")
            
            if os.path.exists(output_file):
                try:
                    df_old = pd.read_excel(output_file)
                    df_final = pd.concat([df_old, df_new], ignore_index=True)
                except:
                    df_final = df_new
            else:
                df_final = df_new
            df_final.to_excel(output_file, index=False)
            print(f"Saved to {output_file}")
    
    # Internal cleanup call
    try:
        from cleanup_manager import close_chagee_windows
        close_chagee_windows()
    except ImportError:
        pass
