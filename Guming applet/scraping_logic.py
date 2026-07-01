import uiautomation as auto
import time
import os
import cv2
import numpy as np
import pandas as pd
from datetime import datetime
from collections import Counter
from paddleocr import PaddleOCR
import logging
import re

logger = logging.getLogger(__name__)

# Import config
import config

class GumingOCRExtractor:
    def __init__(self, lang='ch'):
        self.lang = lang
        # Initialize PaddleOCR
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False, use_gpu=False)

    def _load_image(self, path):
        with open(path, 'rb') as f:
            img_array = np.frombuffer(f.read(), np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    def _preprocess_for_ocr(self, img):
        # Upscale for better OCR text detection
        img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return img

    def clean_store_name(self, text):
        """Cleans and normalizes Guming store names by stripping stars and labels."""
        # Strip special characters and typical label prefixes
        text = text.replace('☆', '').replace('★', '').replace('★', '')
        text = text.replace('现磨咖啡', '').replace('咖啡', '').replace('现磨', '')
        # Remove any non-alphanumeric and non-chinese characters
        text = "".join([c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum() or c in ("-", "_")])
        return text.strip()

    def parse_status(self, raw):
        """Parses order status text to extract cup count, order count, and normalized status."""
        # Normalize common OCR character mistakes
        normalized = raw.replace('卵', '即').replace('刻', '制').replace('佛', '作')
        normalized = normalized.replace('下羡', '下单').replace('析', '6').replace('怀', '坏')
        normalized = normalized.replace('广单', '下单').replace('林', '杯')
        
        cup_count = 0
        order_count = 0
        
        # Look for cup count e.g., "前方3杯/1单，制作中" or "前方6杯/4单"
        # Match digits followed by '杯'
        digits = re.findall(r'(\d+)\s*杯', normalized)
        if digits:
            cup_count = int(digits[0])
            # Check if there is also order counts
            order_digits = re.findall(r'(\d+)\s*单', normalized)
            if order_digits:
                order_count = int(order_digits[0])
                return f"前方{cup_count}杯/{order_count}单，制作中", cup_count, order_count
            else:
                return f"前方{cup_count}杯制作中", cup_count, 0
                
        # Check for "下单立即制作" or "立即制作"
        if any(k in normalized for k in ['立即制作', '下单立即', '立即', '下单', '制作中']):
            return "下单立即制作", 0, 0
            
        # Check for closed stores
        if any(k in normalized for k in ['已休息', '休息', '打烊', '明天再来']):
            return "已休息", 0, 0
            
        # Fallback cleaning
        cleaned = "".join([c for c in normalized if '\u4e00' <= c <= '\u9fff' or c.isalnum()])
        return cleaned if cleaned else "已休息", 0, 0

    def extract_from_box_full(self, box_img):
        """Pattern detection within a single store card image."""
        proc = self._preprocess_for_ocr(box_img)
        res_list = self.ocr.ocr(proc, det=True, cls=True)
        if not res_list or not res_list[0]:
            return "", "OCR Failed", 0, 0
            
        store_name = ""
        order_status = "已休息"
        cup_count = 0
        order_count = 0
        
        # Sort by Y-coordinate so the first items are at the top (typically the store name)
        boxes = res_list[0]
        boxes.sort(key=lambda x: x[0][0][1])
        
        # 1. Store Name is the first line that is NOT a noise keyword
        for box in boxes:
            text = box[1][0].strip()
            clean = self.clean_store_name(text)
            if len(clean) >= 3 and not any(k in clean for k in ['展开地图', '收起地图', '选择门店', '点单', '去下单', '营业']):
                store_name = clean
                break
                
        # 2. Look for order status patterns
        for box in boxes:
            text = box[1][0].strip()
            status_text, count, ord_count = self.parse_status(text)
            # Check if this text matched a valid status pattern
            if count > 0 or any(k in status_text for k in ["前方", "制作中", "立即制作", "已休息"]):
                # Ensure it's not the store name itself
                if status_text != store_name:
                    order_status = status_text
                    cup_count = count
                    order_count = ord_count
                    break
                    
        return store_name, order_status, cup_count, order_count

    def extract_data(self, image_path):
        """Uses OpenCV to crop horizontal store cards and extracts data from each."""
        img = self._load_image(image_path)
        if img is None:
            return [], []

        h_full, w_full = img.shape[:2]
        # Ignore top elements (Search, map collapse, header tabs)
        roi_y = int(config.ROI_TOP_IGNORE_HEIGHT)
        roi = img[roi_y:, :]
        
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 20, 100)
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=1) 
        
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Guming store box dimensions filters
            if config.BOX_MIN_WIDTH <= w <= config.BOX_MAX_WIDTH and \
               config.BOX_MIN_HEIGHT <= h <= config.BOX_MAX_HEIGHT:
                boxes.append((w * h, (x, y + roi_y, w, h)))
                
        # Sort by area descending to deduplicate overlaps
        boxes.sort(key=lambda x: x[0], reverse=True)
        
        final_boxes = []
        for area, b in boxes:
            is_overlap = False
            b_y1, b_y2 = b[1], b[1] + b[3]
            for fb in final_boxes:
                fb_y1, fb_y2 = fb[1], fb[1] + fb[3]
                overlap = max(0, min(b_y2, fb_y2) - max(b_y1, fb_y1))
                if overlap > 0:
                    smaller_h = min(b[3], fb[3])
                    if overlap / smaller_h > 0.5:
                        is_overlap = True
                        break
            if not is_overlap:
                final_boxes.append(b)
                
        # Sort top-to-bottom
        final_boxes.sort(key=lambda b: b[1])
        print(f"Detected {len(final_boxes)} store cards in screenshot.")
        
        extracted_data = []
        ocr_debug_log = []
        
        for i, (bx, by, bw, bh) in enumerate(final_boxes):
            box_img = img[by:by + bh, bx:bx + bw]
            
            store_name, order_status, cup_count, order_count = self.extract_from_box_full(box_img)
            
            # Anti-noise checks
            if not store_name or len(store_name) < 3:
                ocr_debug_log.append(f"  [Box {i}] Skipped — invalid name: '{store_name}'")
                continue
                
            if any(k in store_name for k in ['外卖', '自取', '筛选', '搜索', '订单', '客服']):
                ocr_debug_log.append(f"  [Box {i}] Skipped — noise keyword in name: '{store_name}'")
                continue
                
            ocr_debug_log.append(f"  [Box {i}] Extracted: '{store_name}' | '{order_status}' | {cup_count} cups | {order_count} orders")
            
            # --- Save verified screenshot on threshold ---
            threshold = config.CUP_COUNT_THRESHOLD
            if config.SCREENSHOT_ON_THRESHOLD and cup_count >= threshold:
                try:
                    p_dir = os.path.dirname(os.path.abspath(__file__))
                    d_dir = os.path.join(p_dir, config.DATA_FOLDER)
                    if not os.path.exists(d_dir): 
                        os.makedirs(d_dir)
                    
                    c_name = "".join([c for c in store_name if c.isalnum() or c in (" ", "-", "_")])
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    f_name = f"{c_name}_{cup_count}cups_{ts}.png"
                    
                    # Save image encoding safe
                    _, img_encode = cv2.imencode('.png', box_img)
                    with open(os.path.join(d_dir, f_name), 'wb') as f:
                        f.write(img_encode)
                    print(f"  [!] High cup count screenshot saved: {f_name}")
                except Exception as e:
                    print(f"  [!] Failed to save screenshot: {e}")
                    
            extracted_data.append({
                "store_name": store_name,
                "order_status": order_status,
                "cup_count": cup_count,
                "order_count": order_count
            })
            
        return extracted_data, ocr_debug_log

    def ocr_full_image(self, image_path):
        img = self._load_image(image_path)
        if img is None:
            return []
        res = self.ocr.ocr(img, det=True, cls=True)
        return res[0] if res else []

def collapse_map_if_needed(applet_window, extractor):
    """Checks if Tencent Map widget is showing, and clicks '收起地图' to collapse it."""
    print("Checking if map widget is expanded...")
    rect = applet_window.BoundingRectangle
    project_dir = os.path.dirname(os.path.abspath(__file__))
    temp_img = os.path.join(project_dir, "temp_map_check.png")
    
    applet_window.CaptureToImage(temp_img)
    results = extractor.ocr_full_image(temp_img)
    
    click_x, click_y = -1, -1
    for res in results:
        box, (text, score) = res
        if config.MAP_COLLAPSE_KEYWORD in text:
            cx = (box[0][0] + box[2][0]) / 2.0
            cy = (box[0][1] + box[2][1]) / 2.0
            click_x = int(rect.left + cx)
            click_y = int(rect.top + cy)
            print(f"OCR found collapse keyword '{text}' at local ({cx}, {cy}). Absolute click: ({click_x}, {click_y})")
            break
            
    if click_x == -1:
        # Fallback to direct coordinates if OCR fails
        # Let's see if we see "展开地图". If "展开地图" is present, it is already collapsed.
        is_collapsed = any("展开地图" in res[1][0] for res in results)
        if is_collapsed:
            print("Map is already collapsed.")
            return True
        else:
            print("OCR did not find collapse keyword. Using configuration fallback.")
            click_x = rect.left + config.MAP_COLLAPSE_COORD[0]
            click_y = rect.top + config.MAP_COLLAPSE_COORD[1]
            
    auto.Click(click_x, click_y)
    time.sleep(0.8)
    return True

def switch_city(applet_window, city_name, extractor):
    """Navigates the city list, types the query in Guming search, and clicks matching city."""
    print(f"\n--- Initiating Switch to {city_name} ---")
    try:
        applet_window.SetActive()
        time.sleep(0.5)
        rect = applet_window.BoundingRectangle
    except Exception as e:
        print(f"[!] COMError/Exception on switch_city SetActive: {e}. Re-fetching window...")
        found_win = None
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                found_win = window
                break
        if found_win:
            applet_window = found_win
            applet_window.SetActive()
            time.sleep(0.5)
            rect = applet_window.BoundingRectangle
        else:
            raise RuntimeError("Failed to re-fetch Guming applet window in switch_city.")
    
    # 1. Click city selector trigger at (45, 152) relative
    trigger_x = rect.left + config.CITY_SELECTOR_TRIGGER[0]
    trigger_y = rect.top + config.CITY_SELECTOR_TRIGGER[1]
    print(f"Clicking city selector trigger at absolute ({trigger_x}, {trigger_y})")
    auto.Click(trigger_x, trigger_y)
    time.sleep(1.0)
    
    # 2. Click search input box at (208, 92) relative
    search_x = rect.left + config.CITY_SEARCH_INPUT_COORD[0]
    search_y = rect.top + config.CITY_SEARCH_INPUT_COORD[1]
    print(f"Clicking city search input at absolute ({search_x}, {search_y})")
    auto.Click(search_x, search_y)
    time.sleep(0.2)
    
    # Clear input and type city
    for _ in range(10):
        auto.SendKeys('{Back}')
    time.sleep(0.2)
    auto.SendKeys(city_name)
    print(f"Typed city: {city_name}. Waiting for query filtering...")
    time.sleep(2.0)
    
    # 3. Capture screen and use OCR to click matching city name
    project_dir = os.path.dirname(os.path.abspath(__file__))
    temp_img = os.path.join(project_dir, "temp_city_search.png")
    applet_window.CaptureToImage(temp_img)
    results = extractor.ocr_full_image(temp_img)
    
    click_city_x, click_city_y = -1, -1
    for res in results:
        box, (text, score) = res
        # Match city name (ignore the top search input box Y-coordinate)
        if city_name in text:
            cy_local = (box[0][1] + box[2][1]) / 2.0
            cx_local = (box[0][0] + box[2][0]) / 2.0
            if cy_local > 120:
                click_city_x = int(rect.left + cx_local)
                click_city_y = int(rect.top + cy_local)
                print(f"OCR found city matching query: '{text}' at absolute ({click_city_x}, {click_city_y})")
                break
                
    if click_city_x != -1:
        auto.Click(click_city_x, click_city_y)
        print("City clicked. Waiting 4s for store list to refresh...")
        time.sleep(4.0)
        return True
    else:
        print(f"Failed to find city '{city_name}' in search results.")
        # Try clicking go back to prevent getting stuck
        auto.Click(rect.left + 20, rect.top + 40)
        time.sleep(1)
        return False

def log_applet_screen(applet_window, extractor, step_name):
    """Captures a debug screenshot and dumps all visible text elements for tracing."""
    try:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        debug_dir = os.path.join(project_dir, "debug_logs")
        if not os.path.exists(debug_dir):
            os.makedirs(debug_dir)
            
        safe_step = "".join([c for c in step_name if c.isalnum() or c in ("-", "_", " ")]).strip().replace(" ", "_")
        ts = datetime.now().strftime("%H%M%S")
        img_path = os.path.join(debug_dir, f"debug_{ts}_{safe_step}.png")
        
        applet_window.CaptureToImage(img_path)
        results = extractor.ocr_full_image(img_path)
        
        print(f"\n--- [DEBUG SCREEN: {step_name}] (Image: {img_path}) ---")
        texts = []
        for box, (text, score) in results:
            cx = (box[0][0] + box[2][0]) / 2.0
            cy = (box[0][1] + box[2][1]) / 2.0
            print(f"  * Text: '{text}' | Confidence: {score:.2f} | Local: ({cx:.1f}, {cy:.1f})")
            texts.append(text)
        print("--------------------------------------------------\n")
        return img_path, texts
    except Exception as e:
        print(f"[!] Debug screen logger failed for {step_name}: {e}")
        return None, []

def find_first_suggestion_coordinate(applet_window, extractor, sub_region):
    """Takes a screenshot, uses OCR to locate the first suggestion block containing the sub_region,
    and returns its absolute coordinates. Falls back to a default coordinate if not found."""
    try:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        temp_img = os.path.join(project_dir, "temp_suggestion_ocr.png")
        applet_window.CaptureToImage(temp_img)
        results = extractor.ocr_full_image(temp_img)
        rect = applet_window.BoundingRectangle
        
        # We search for the first text item that contains the sub-region (or a key part of it)
        # below the search input field height (local y > 180) and above footer (y < 750)
        keyword_part = sub_region[:-1] if len(sub_region) > 2 else sub_region
        for box, (text, score) in results:
            cx_local = (box[0][0] + box[2][0]) / 2.0
            cy_local = (box[0][1] + box[2][1]) / 2.0
            if 180 < cy_local < 750:
                if keyword_part in text:
                    click_x = int(rect.left + cx_local)
                    click_y = int(rect.top + cy_local)
                    print(f"OCR found matching suggestion text '{text}' at local ({cx_local:.1f}, {cy_local:.1f}). Absolute click: ({click_x}, {click_y})")
                    return click_x, click_y
                        
        # Secondary scan: if we found "地理位置" (Geographic Location) header, the first address suggestion
        # is typically about 35px below the header.
        for box, (text, score) in results:
            cx_local = (box[0][0] + box[2][0]) / 2.0
            cy_local = (box[0][1] + box[2][1]) / 2.0
            if 180 < cy_local < 750:
                if "地理位置" in text or "地理" in text:
                    click_x = int(rect.left + 200)
                    click_y = int(rect.top + cy_local + 35) # 35px below header
                    print(f"OCR found '地理位置' header. Clicking 35px below at absolute ({click_x}, {click_y})")
                    return click_x, click_y

        # Fallback to local coordinates y = 270 (center of first suggestion item)
        fallback_x = rect.left + 200
        fallback_y = rect.top + 270
        print(f"OCR did not find suggestion matching '{sub_region}'. Fallback click at absolute ({fallback_x}, {fallback_y})")
        return fallback_x, fallback_y
    except Exception as e:
        rect = applet_window.BoundingRectangle
        fallback_x = rect.left + 200
        fallback_y = rect.top + 270
        print(f"[!] Error in suggestion OCR lookup: {e}. Fallback click at absolute ({fallback_x}, {fallback_y})")
        return fallback_x, fallback_y

def scrape_city_stores(applet_window, extractor, target_count, city_name, sub_regions=[], click_entry=True):
    """Scrolls and scrapes the Guming store cards in the selected city, supporting sub-regions."""
    print(f"\n--- Scraping Guming City: {city_name} (Target: {target_count}, Sub-regions: {sub_regions}) ---")
    try:
        applet_window.SetActive()
        time.sleep(0.5)
        rect = applet_window.BoundingRectangle
    except Exception as e:
        print(f"[!] COMError/Exception on scrape_city_stores SetActive: {e}. Re-fetching window...")
        found_win = None
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                found_win = window
                break
        if found_win:
            applet_window = found_win
            applet_window.SetActive()
            time.sleep(0.5)
            rect = applet_window.BoundingRectangle
        else:
            raise RuntimeError("Failed to re-fetch Guming applet window in scrape_city_stores.")
    
    if click_entry:
        # Click "点单" tab at bottom
        entry_x = rect.left + config.STORE_LIST_ENTRY_REL_COORD[0]
        entry_y = rect.top + config.STORE_LIST_ENTRY_REL_COORD[1]
        print(f"Clicking '点单' tab -> absolute ({entry_x}, {entry_y})")
        auto.Click(entry_x, entry_y)
        time.sleep(2.0)
        
    project_dir = os.path.dirname(os.path.abspath(__file__))
    screenshot_path = os.path.join(project_dir, "temp_scrape.png")
    scroll_x = rect.left + config.SCROLL_REL_COORD[0]
    scroll_y = rect.top + config.SCROLL_REL_COORD[1]
    
    # Internal helper function to scrape the visible scrollable list
    def collect_from_current_view(sub_label):
        # Collapse map if needed to expose full scrolling list
        collapse_map_if_needed(applet_window, extractor)
        
        view_results = {}
        consecutive_no_new = 0
        max_no_new_scrolls = config.MAX_NO_NEW_SCROLLS
        
        # Reset scroll to top
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelUp(wheelTimes=10, interval=0.1)
        time.sleep(0.5)
        auto.WheelDown(wheelTimes=2, interval=0.1)
        time.sleep(0.8)
        
        while len(view_results) < target_count and consecutive_no_new < max_no_new_scrolls:
            applet_window.CaptureToImage(screenshot_path)
            results, ocr_debug_log = extractor.extract_data(screenshot_path)
            
            new_found = 0
            for res in results:
                name = res['store_name']
                if name not in view_results:
                    res['City'] = city_name
                    view_results[name] = res
                    new_found += 1
                    print(f"  [+] {city_name} ({sub_label}): {name} ({res['order_status']})")
                    
            if new_found > 0:
                consecutive_no_new = 0
            else:
                consecutive_no_new += 1
                print(f"  [!] No new stores found ({consecutive_no_new}/{max_no_new_scrolls}).")
                
            if len(view_results) >= target_count:
                break
                
            auto.MoveTo(scroll_x, scroll_y)
            auto.WheelDown(wheelTimes=config.SCROLL_WHEEL_TIMES, interval=0.1)
            time.sleep(0.8)
            
        if consecutive_no_new >= max_no_new_scrolls:
            print(f"  [!] Reached scroll limit for {city_name} ({sub_label}).")
        return list(view_results.values())

    # Case 1: No sub-regions specified - scrape the city directly
    if not sub_regions:
        collapse_map_if_needed(applet_window, extractor)
        res = collect_from_current_view("City-Wide")
        print(f"Scraped {len(res)} stores in {city_name}.")
        return res

    # Case 2: Sub-regions specified - loop and search each
    aggregated_results = []
    for sub_region in sub_regions:
        print(f"\n--- Searching sub-region: {sub_region} in {city_name} ---")
        
        # Find EditControl with polling
        edit = applet_window.EditControl()
        edit_found = False
        for _ in range(10):
            if edit.Exists(0.1):
                edit_found = True
                break
            time.sleep(0.5)
            
        if not edit_found:
            print(f"Could not find search EditControl for sub-region {sub_region}. Skipping.")
            continue
            
        # Click to focus
        edit.Click()
        time.sleep(0.5)
        
        # Clear value by sending backspaces (safest for IME environments)
        for _ in range(15):
            auto.SendKeys('{Back}', waitTime=0.05)
        time.sleep(0.2)
        
        # Type sub-region using simulated keyboard keys to trigger JS events
        print(f"Typing keyword: {sub_region}...")
        auto.SendKeys(sub_region, waitTime=0.1)
        
        # Wait 1.0 second for the search suggestion list to show
        print("Waiting 1s for suggestions list to show...")
        time.sleep(1.0)
        
        # [DEBUG LOGGER] Check screen after typing sub-region
        log_applet_screen(applet_window, extractor, f"{sub_region}_typed")
        
        # Click the first suggestion option dynamically located via OCR
        suggestion_x, suggestion_y = find_first_suggestion_coordinate(applet_window, extractor, sub_region)
        print(f"Clicking the suggestion option at absolute ({suggestion_x}, {suggestion_y})...")
        auto.Click(suggestion_x, suggestion_y)
        
        # Wait 2 seconds for the store list page to refresh and settle
        time.sleep(2.0)
        
        # [DEBUG LOGGER] Check screen after selecting suggestion (should be store list with map again)
        log_applet_screen(applet_window, extractor, f"{sub_region}_selected_suggestion")
        
        # Scrape this sub-region's list
        sub_res = collect_from_current_view(sub_region)
        aggregated_results.extend(sub_res)
        
        # [DEBUG LOGGER] Check screen after scrolling/scraping is done
        log_applet_screen(applet_window, extractor, f"{sub_region}_scraped")
        
        # Click Cancel/取消 to return to default list
        cancel_clicked = False
        cancel_btn = applet_window.TextControl(Name="取消")
        if cancel_btn.Exists(1.0):
            cancel_btn.Click()
            cancel_clicked = True
        else:
            # Fallback to OCR "取消" button coordinates
            project_dir = os.path.dirname(os.path.abspath(__file__))
            temp_img = os.path.join(project_dir, "temp_cancel_ocr.png")
            applet_window.CaptureToImage(temp_img)
            results = extractor.ocr_full_image(temp_img)
            for box, (text, score) in results:
                if "取消" in text:
                    cx_local = (box[0][0] + box[2][0]) / 2.0
                    cy_local = (box[0][1] + box[2][1]) / 2.0
                    click_x = int(rect.left + cx_local)
                    click_y = int(rect.top + cy_local)
                    print(f"OCR found '取消' button at local ({cx_local:.1f}, {cy_local:.1f}). Clicking absolute ({click_x}, {click_y})")
                    auto.Click(click_x, click_y)
                    cancel_clicked = True
                    break
            if not cancel_clicked:
                fallback_x = rect.left + 387
                fallback_y = rect.top + 150
                print(f"OCR did not find '取消'. Clicking fallback absolute ({fallback_x}, {fallback_y})")
                auto.Click(fallback_x, fallback_y)
        time.sleep(2.0)
        
        # [DEBUG LOGGER] Check screen after returning to default list
        log_applet_screen(applet_window, extractor, f"{sub_region}_cancelled")

    # Deduplicate aggregated results, keeping the record with the largest cup count if repeated
    deduped = {}
    for r in aggregated_results:
        name = r['store_name']
        if name not in deduped:
            deduped[name] = r
        else:
            if r.get('cup_count', 0) > deduped[name].get('cup_count', 0):
                deduped[name] = r
                
    final_res = list(deduped.values())
    print(f"Aggregated {len(aggregated_results)} stores. Deduplicated to {len(final_res)} unique stores for {city_name}.")
    return final_res

def main_workflow():
    # Helper workflow to run standalone testing
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
            applet_window = window
            break
    else:
        print("Applet window not found.")
        return
        
    extractor = GumingOCRExtractor()
    scrape_city_stores(applet_window, extractor, 10, "杭州", click_entry=False)

if __name__ == "__main__":
    main_workflow()
