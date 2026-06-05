import uiautomation as auto
import time
import os
import sys
import cv2
import numpy as np
import pandas as pd
from datetime import datetime
from collections import Counter
from paddleocr import PaddleOCR
import json
from pypinyin import pinyin, Style
import logging

logger = logging.getLogger(__name__)

# Import configuration
import config
from config import CITY_LIST

# --- Group 2: Chagee Applet Navigation and Main Scraping Logic ---

class ChageeOCRExtractor:
    def __init__(self, lang='ch'):
        self.lang = lang
        # Initialize PaddleOCR
        # use_angle_cls=True helps with slightly rotated text
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False, use_gpu=False)

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
            return [], []

        # 1. Try Old Method (OpenCV boxes + Method 1 & 2)
        extracted_data, ocr_debug_log = self._extract_data_old(img, image_path)
        
        # 2. Fallback to New Method (Full-Page OCR) if old method underperformed
        if len(extracted_data) <= 2:
            ocr_debug_log.append("  [!] Old method yielded few/no results. Falling back to New Method (Full-Page OCR)...")
            new_data, new_log = self._extract_data_new(img, image_path)
            
            # Use new method if it finds strictly more stores
            if len(new_data) > len(extracted_data):
                ocr_debug_log.append("  [!] New method successfully found more stores. Using new method.")
                return new_data, ocr_debug_log + new_log
            else:
                ocr_debug_log.append("  [!] New method did not find more stores. Sticking with old method.")

        return extracted_data, ocr_debug_log

    def _extract_data_old(self, img, image_path):
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
        
        # Sort by area descending so we process larger boxes first in deduplication
        boxes.sort(key=lambda x: x[0], reverse=True)
        
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
        ocr_debug_log = []
        debug_base = os.path.join(os.path.dirname(image_path), f"debug_{os.path.basename(image_path)}")
        if not os.path.exists(debug_base):
            os.makedirs(debug_base)

        for i, (bx, by, bw, bh) in enumerate(final_boxes):
            box_img = img[by:by + bh, bx:bx + bw]
            self._save_image(os.path.join(debug_base, f"box_{i}_full.png"), box_img)

            # --- Step A: Dual-Method OCR (Legacy & Full-Box) ---
            # 1. Try Method 2 (Full Box Pattern Detection)
            res2_name, res2_status, res2_count = self.extract_from_box_full(box_img)
            valid2 = self.is_valid_result(res2_name, res2_status)
            ocr_debug_log.append(f"  [Box {i}] Method2 OCR: name='{res2_name}' status='{res2_status}' count={res2_count} valid={valid2}")

            # 2. Try Method 1 (Legacy Manual Crop)
            sn_y_start, sn_h = config.SN_CROP_Y_START, config.SN_CROP_HEIGHT
            os_y_start, os_h = config.OS_CROP_Y_START, config.OS_CROP_HEIGHT
            y1, y2 = min(sn_y_start, bh-1), min(sn_y_start + sn_h, bh)
            y3, y4 = min(os_y_start, bh-1), min(os_y_start + os_h, bh)
            sn_roi = box_img[y1:y2, 10 : int(bw * config.BOX_WIDTH_CUTOFF_PERCENT)]
            os_roi = box_img[y3:y4, 10 : int(bw * config.BOX_WIDTH_CUTOFF_PERCENT)]

            res1_name, res1_status, res1_count = "", "", 0
            valid1 = False
            if sn_roi.size > 0 and os_roi.size > 0:
                sn_res = self.ocr.ocr(self._preprocess_for_ocr(sn_roi), det=False, cls=True)
                res1_name = self.clean_store_name(sn_res[0][0][0] if sn_res and sn_res[0] else "")
                os_res = self.ocr.ocr(self._preprocess_for_ocr(os_roi), det=False, cls=True)
                res1_status, res1_count = self.parse_status(os_res[0][0][0] if os_res and os_res[0] else "")
                valid1 = self.is_valid_result(res1_name, res1_status)
                ocr_debug_log.append(f"  [Box {i}] Method1 OCR: name='{res1_name}' status='{res1_status}' count={res1_count} valid={valid1}")

            # --- Step B: Selection Logic ---
            if valid2:
                # Prefer Full Box if it succeeded
                store_name, order_status, cup_count = res2_name, res2_status, res2_count
                winning_method = 2
            elif valid1:
                # Fallback to Legacy if Full Box failed but Legacy succeeded
                store_name, order_status, cup_count = res1_name, res1_status, res1_count
                winning_method = 1
            else:
                # Both methods failed pattern checks
                ocr_debug_log.append(f"  [Box {i}] Skipped — both methods invalid")
                continue

            ocr_debug_log.append(f"  [Box {i}] Using Method{winning_method}: '{store_name}' | '{order_status}' | {cup_count} cups")

            # General Anti-noise
            if any(k in store_name for k in ['外卖', '到店', '自取', '筛选', '搜索', '在售']):
                ocr_debug_log.append(f"  [Box {i}] Skipped — noise keyword in name")
                continue

            # --- Step C: High Threshold Verification (4 Samples) ---
            threshold = getattr(config, 'CUP_COUNT_THRESHOLD', 80)
            if cup_count >= threshold:
                print(f"  [?] High value ({cup_count}). Verifying with Method {winning_method}...")
                samples = [cup_count]
                
                for scale in [3, 5, 2]:
                    if winning_method == 2:
                        _, _, count_alt = self.extract_from_box_full(box_img, scale=scale)
                    else:
                        proc_alt = cv2.resize(os_roi, (0,0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                        ocr_alt = self.ocr.ocr(proc_alt, det=False, cls=True)
                        raw_alt = ocr_alt[0][0][0] if ocr_alt and ocr_alt[0] else ""
                        _, count_alt = self.parse_status(raw_alt)
                    samples.append(count_alt)
                
                cup_count = Counter(samples).most_common(1)[0][0]
                order_status = f"前方{cup_count}杯制作中" if cup_count > 0 else "现在下单，立即制作"
                print(f"  [√] Verified result: {cup_count} (Samples: {samples})")

            # --- Step D: Threshold Screenshots ---
            if getattr(config, 'SCREENSHOT_ON_THRESHOLD', False) and cup_count >= threshold:
                try:
                    p_dir = os.path.dirname(os.path.abspath(__file__))
                    d_dir = os.path.join(p_dir, getattr(config, 'DATA_FOLDER', 'data'))
                    if not os.path.exists(d_dir): os.makedirs(d_dir)
                    
                    c_name = "".join([c for c in store_name if c.isalnum() or c in (" ", "-", "_")])
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    f_name = f"{c_name}_{cup_count}cups_{ts}.png"
                    self._save_image(os.path.join(d_dir, f_name), box_img)
                    print(f"  [!] Saved verified screenshot: {f_name}")
                except Exception as e:
                    print(f"  [!] Screenshot failed: {e}")

            extracted_data.append({
                "store_name": store_name,
                "order_status": order_status,
                "cup_count": cup_count,
                "debug_sn": os.path.join(debug_base, f"box_{i}_sn_crop.png") if winning_method == 1 else "",
                "debug_os": os.path.join(debug_base, f"box_{i}_os_crop.png") if winning_method == 1 else ""
            })

        
        return extracted_data, ocr_debug_log

    def _extract_data_new(self, img, image_path):
        h_full, w_full = img.shape[:2]
        roi_y = int(h_full * config.ROI_TOP_IGNORE_PERCENT)
        roi = img[roi_y:, :]
        
        # Method 3: Full Page OCR and Y-Coordinate Clustering
        res_list = self.ocr.ocr(roi, det=True, cls=True)
        if not res_list or not res_list[0]:
            print(f"No OCR results in {os.path.basename(image_path)}")
            return [], []

        boxes = res_list[0]
        # Sort boxes by top-left y coordinate
        boxes.sort(key=lambda x: x[0][0][1])

        extracted_data = []
        ocr_debug_log = []
        
        names = []
        statuses = []
        
        for i, box in enumerate(boxes):
            coords = box[0]
            text, score = box[1]
            y_top = coords[0][1]
            y_bottom = coords[2][1]
            
            # Check if it's a store name
            clean_name = self.clean_store_name(text)
            if clean_name.endswith('店') and len(clean_name) >= 4:
                # Discard noise
                if not any(k in clean_name for k in ['外卖', '到店', '自取', '筛选', '搜索', '在售']):
                    names.append({
                        'text': clean_name,
                        'y': y_top,
                        'y_bottom': y_bottom,
                        'box': coords
                    })
                    
            # Check if it's an order status
            status_text, count = self.parse_status(text)
            if count > 0 or any(k in status_text for k in ["前方", "杯", "制作", "下单"]):
                statuses.append({
                    'text': status_text,
                    'count': count,
                    'y': y_top,
                    'y_bottom': y_bottom,
                    'box': coords,
                    'raw': text
                })

        # Pair names and statuses
        used_statuses = set()
        for name_info in names:
            name_y = name_info['y_bottom']
            best_status = None
            min_dist = float('inf')
            
            for j, status_info in enumerate(statuses):
                if j in used_statuses:
                    continue
                
                status_y = status_info['y']
                # Status must be below the name, but not too far
                dist = status_y - name_y
                if -15 <= dist <= 80: # Threshold for Y distance
                    if dist < min_dist:
                        min_dist = dist
                        best_status = (j, status_info)
                        
            if best_status:
                used_statuses.add(best_status[0])
                status_info = best_status[1]
                
                store_name = name_info['text']
                order_status = status_info['text']
                cup_count = status_info['count']
                
                ocr_debug_log.append(f"  [Found] '{store_name}' | '{order_status}' | {cup_count} cups (dist: {min_dist:.1f})")
                
                # --- High Threshold Verification ---
                threshold = getattr(config, 'CUP_COUNT_THRESHOLD', 80)
                if cup_count >= threshold:
                    print(f"  [?] High value ({cup_count}). Verifying...")
                    samples = [cup_count]
                    
                    # Crop out just the status region from original image (adjusting for ROI)
                    coords = status_info['box']
                    x_coords = [int(c[0]) for c in coords]
                    y_coords = [int(c[1]) for c in coords]
                    x_min, x_max = max(0, min(x_coords)-5), min(w_full, max(x_coords)+5)
                    y_min, y_max = max(0, min(y_coords)-5 + roi_y), min(h_full, max(y_coords)+5 + roi_y)
                    
                    status_img = img[y_min:y_max, x_min:x_max]
                    
                    for scale in [3, 5, 2]:
                        if status_img.size > 0:
                            proc_alt = cv2.resize(status_img, (0,0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                            ocr_alt = self.ocr.ocr(proc_alt, det=False, cls=True)
                            raw_alt = ocr_alt[0][0][0] if ocr_alt and ocr_alt[0] else ""
                            _, count_alt = self.parse_status(raw_alt)
                            samples.append(count_alt)
                    
                    cup_count = Counter(samples).most_common(1)[0][0]
                    order_status = f"前方{cup_count}杯制作中" if cup_count > 0 else "现在下单，立即制作"
                    print(f"  [√] Verified result: {cup_count} (Samples: {samples})")

                extracted_data.append({
                    "store_name": store_name,
                    "order_status": order_status,
                    "cup_count": cup_count,
                    "debug_sn": "",
                    "debug_os": ""
                })
            else:
                ocr_debug_log.append(f"  [Skipped] Found name '{name_info['text']}' but no valid status near it.")

        print(f"Detected {len(names)} names and paired {len(extracted_data)} stores in {os.path.basename(image_path)} (New Method)")
        return extracted_data, ocr_debug_log

    def is_valid_result(self, name, status):
        """Universal pattern check for store name and order status."""
        # Rule 1: Store Name MUST end with '店' and be long enough
        if not name.endswith('店') or len(name) < 4:
            return False
            
        # Rule 2: Order Status MUST match the patterns
        is_pattern = (status.endswith("制作中") and "前方" in status) or \
                      (status == "现在下单，立即制作")
        return is_pattern

    def parse_status(self, raw):
        """Parses order status text to extract cup count and normalized status."""
        # Status-specific misrecognitions: '林' often read as '杯'
        normalized = raw.replace('卵', '即').replace('刻', '制').replace('佛', '作').replace('下羡', '下单').replace('析', '6').replace('怀', '坏').replace('广单', '下单').replace('林', '杯')
        
        import re
        digits = re.findall(r'\d+', normalized)
        cup_count = 0
        
        # Rule: Strict format check for "前方x杯制作中"
        if digits and '杯' in normalized and (any(k in normalized for k in ['前方', '制作', '中', '制'])):
            cup_count = int(digits[0])
            return f"前方{cup_count}杯制作中", cup_count
        
        # Rule: Check for "现在下单，立即制作"
        if any(k in normalized for k in ['立即制作', '现在下单', '立即', '下单', '制作中']):
            return "现在下单，立即制作", 0
        
        # Fallback: Clean and return as 0
        cleaned = "".join([c for c in normalized if '\u4e00' <= c <= '\u9fff' or c.isalnum()])
        return cleaned if cleaned else "已休息", 0

    def clean_store_name(self, text):
        """Cleans and normalizes store names (Shared for Method 1 & 2)."""
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
        
        # If '店' is second to last, remove the last character
        if len(text) >= 2 and text[-2] == '店':
            text = text[:-1]

        return text.strip()

    def extract_from_box_full(self, box_img, scale=4):
        """Method 2: Full box pattern detection without manual cropping."""
        # Preprocess
        proc = cv2.resize(box_img, (0,0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        
        # OCR with detection (det=True) to find parts
        res_list = self.ocr.ocr(proc, det=True, cls=True)
        if not res_list or not res_list[0]:
            return "", "OCR Failed", 0
            
        store_name = ""
        order_status = "已休息"
        cup_count = 0
        
        # Sort results by Y coordinate to help identify store name vs status
        # PaddleOCR format: [ [[ [x1,y1]... ], (text, score)] ... ]
        boxes = res_list[0]
        boxes.sort(key=lambda x: x[0][0][1]) # Sort by top-left y
        
        # 1. Look for store name (ends with '店', must be below y=24 in original box coords)
        min_y_scaled = 24 * scale
        for box in boxes:
            y_top = box[0][0][1]
            if y_top < min_y_scaled:
                continue
            text = box[1][0]
            if text.endswith('店'):
                store_name = self.clean_store_name(text)
                break
        
        # 2. Look for order status (patterns)
        for box in boxes:
            text = box[1][0]
            status_text, count = self.parse_status(text)
            if count > 0 or any(k in status_text for k in ["前方", "杯", "制作", "下单"]):
                order_status = status_text
                cup_count = count
                break
                
        return store_name, order_status, cup_count

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
        
        results, ocr_debug_log = extractor.extract_data(screenshot_path)
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
            for line in ocr_debug_log:
                print(line)
            
        if len(city_results) >= target_count: break
            
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelDown(wheelTimes=config.SCROLL_WHEEL_TIMES, interval=0.1)
        time.sleep(2)

    if consecutive_no_new >= max_no_new_scrolls:
        # Note it as a BUG and save screenshot for debugging
        print(f"  [!] BUG: Aborted {city_name}: Reached limit of {max_no_new_scrolls} scrolls without new data.")
        try:
            p_dir = os.path.dirname(os.path.abspath(__file__))
            d_dir = os.path.join(p_dir, getattr(config, 'DATA_FOLDER', 'data'))
            if not os.path.exists(d_dir): os.makedirs(d_dir)
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bug_screenshot = os.path.join(d_dir, f"BUG_scroll_limit_{city_name}_{ts}.png")
            applet_window.CaptureToImage(bug_screenshot)
            print(f"  [!] Saved bug debugging screenshot: {bug_screenshot}")
        except Exception as e:
            print(f"  [!] Bug screenshot capture failed: {e}")

    print(f"Scraped {len(city_results)} stores in {city_name}.")
    return list(city_results.values())

def main_workflow():
    applet_window = get_applet_window()
    if not applet_window:
        print("Applet window not found.")
        return

    applet_window.SetActive()
    time.sleep(1)

    if getattr(config, 'TURN_OFF_BLUR', False):
        project_dir = os.path.dirname(os.path.abspath(__file__))
        blur_check_img_path = os.path.join(project_dir, "temp_blur_check.png")
        applet_window.CaptureToImage(blur_check_img_path)
        
        try:
            with open(blur_check_img_path, 'rb') as f:
                img_array = np.frombuffer(f.read(), np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.error(f"Error reading blur check image: {e}")
            img = None

        if img is not None:
            # Region (x1=20, y1=600) to (x2=60, y2=650)
            region = img[600:650, 20:60]
            if region.size > 0:
                avg_bgr = np.mean(region, axis=(0, 1))
                if np.all(avg_bgr < 100):
                    logger.info(f"Blur check status: Blur detected (avg BGR: {avg_bgr}). Turning off blur (clearing pop-up)...")
                    rect = applet_window.BoundingRectangle
                    blur_coord = getattr(config, 'BLUR_CLOSE_COORD', (204, 619))
                    blur_x = rect.left + blur_coord[0]
                    blur_y = rect.top + blur_coord[1]
                    auto.Click(blur_x, blur_y)
                    time.sleep(2)
                else:
                    logger.info(f"Blur check status: No blur detected (avg BGR: {avg_bgr}). Skipping blur turn-off.")
            else:
                logger.warning("Blur check status: Could not extract region for blur check.")
        else:
            logger.warning("Blur check status: Failed to capture image for blur check.")

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
