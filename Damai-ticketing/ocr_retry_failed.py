import cv2
import pytesseract
import numpy as np
import os
import re
import json
from PIL import Image

# === CONFIGURATION ===
IMG_DIR = "scraped_charts"
FAILED_LOG = "ocr_failed_list.txt"             # Read from AND update this file
OUTPUT_FILE = "ocr_refined_data.jsonl"         # Append success here

# === RUN MODES ===
DEBUG_PRINT = False        # Toggle console logs (Raw results)
DEBUG_SAVE_IMAGES = False  # Toggle saving debug images
DEBUG_DIR = "ocr_debug_frames_retry"

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
VALID_KEYS = {"其他", "旅游演艺", "舞蹈", "综艺", "戏剧", "曲杂", "音乐"}

if DEBUG_SAVE_IMAGES and not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

# === HELPERS ===
def clean_key_text(text):
    if not text: return ""
    return re.sub(r'[^\u4e00-\u9fa5]', '', text)

def extract_date_from_filename(filename):
    base = os.path.splitext(filename)[0]
    for suffix in ["_boxoffice", "_audience", "_event"]:
        if base.endswith(suffix):
            return base[:-len(suffix)]
    if "_" in base:
        return base.rsplit('_', 1)[0]
    return base

def save_image_cv2_unicode(filename, img_array):
    if not DEBUG_SAVE_IMAGES: return False
    try:
        is_success, im_buf_arr = cv2.imencode(".png", img_array)
        if is_success:
            im_buf_arr.tofile(filename)
            return True
    except: return False

def is_valid_value(val_str):
    if not val_str: return False
    pattern = re.compile(r'^[0-9\.\+\万\亿]+$') 
    return bool(pattern.match(val_str))

def validate_result(ocr_dict):
    if not ocr_dict: return False
    for k, v in ocr_dict.items():
        if k.strip() not in VALID_KEYS: return False 
        if not is_valid_value(v.strip()): return False
    return True

# === STRATEGIES (0-6) ===
def apply_strategy(img_bgr, strategy_id):
    img = img_bgr.copy()
    h, w = img.shape[:2]
    processed = None

    if strategy_id == 0: # Original Center Cut
        center_fraction = 1/4
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * center_fraction), int(h * center_fraction)
        img[cy - cut_h // 2 : cy + cut_h // 2, cx - cut_w // 2 : cx + cut_w // 2] = 255 
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif strategy_id == 1: # Std Otsu
        center_fraction = 1/4
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * center_fraction), int(h * center_fraction)
        img[cy - cut_h // 2 : cy + cut_h // 2, cx - cut_w // 2 : cx + cut_w // 2] = 255  
        
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif strategy_id == 2: # Binary 127
        center_fraction = 1/4
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * center_fraction), int(h * center_fraction)
        img[cy - cut_h // 2 : cy + cut_h // 2, cx - cut_w // 2 : cx + cut_w // 2] = 255      
        
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 127, 255, cv2.THRESH_BINARY)
    elif strategy_id == 3: # Erode
        center_fraction = 1/4
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * center_fraction), int(h * center_fraction)
        img[cy - cut_h // 2 : cy + cut_h // 2, cx - cut_w // 2 : cx + cut_w // 2] = 255 
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed = cv2.erode(processed, np.ones((2,2), np.uint8), iterations=1)
    elif strategy_id == 4: # Dilate
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed = cv2.dilate(processed, np.ones((2,2), np.uint8), iterations=1)
    elif strategy_id == 5: # Blur + Otsu
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(processed, (5,5), 0)
        _, processed = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif strategy_id == 6: # CLAHE
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        processed = clahe.apply(gray)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if processed is not None:
        if cv2.countNonZero(processed) / processed.size < 0.5:
            processed = cv2.bitwise_not(processed)
    return processed

def run_ocr_on_image_file(filepath, filename_only):
    try:
        img_array = np.fromfile(filepath, np.uint8)
        img_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img_bgr is None: 
             if DEBUG_PRINT: print(f"❌ Failed to decode: {filename_only}")
             return None, False, {}
    except: return None, False, {}

    config_file_path = os.path.abspath("my_whitelist_config")

    strat0_result = {}

    for i in range(7):
        processed_img = apply_strategy(img_bgr, i)
        if processed_img is None: continue

        if DEBUG_SAVE_IMAGES:
             save_image_cv2_unicode(os.path.join(DEBUG_DIR, f"{filename_only}_S{i}.png"), processed_img)

        # OCR
        h, w = processed_img.shape
        mid = w // 2
        # custom_config = r'--psm 6 --dpi 300 -c tessedit_char_blacklist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ一-'
        custom_config = f'--psm 6 --dpi 300 "{config_file_path}"'
        txt_left = pytesseract.image_to_string(processed_img[:, :mid], lang='chi_sim', config=custom_config)
        txt_right = pytesseract.image_to_string(processed_img[:, mid:], lang='chi_sim', config=custom_config)
        
        # Parse
        full_text = txt_left + "\n" + txt_right
        lines = [line.strip().replace(" ", "") for line in full_text.splitlines() if line.strip()]
        
        result = {}
        for j in range(0, len(lines)-1, 2):
            key = clean_key_text(lines[j])
            if key: result[key] = lines[j+1]
        
        # --- DEBUG PRINT ---
        if DEBUG_PRINT:
            print(f"   [Strat {i}] Result: {result}")
        # -------------------

        if i == 0: strat0_result = result

        if validate_result(result):
            if DEBUG_PRINT: print(f"   ✅ Fixed with Strategy {i}")
            return result, True, strat0_result

    return strat0_result, False, strat0_result

# === MAIN RUNNER ===
def main():
    if not os.path.exists(FAILED_LOG):
        print(f"File {FAILED_LOG} not found.")
        return

    # Clear debug folder if saving is enabled
    if DEBUG_SAVE_IMAGES and os.path.exists(DEBUG_DIR):
        for f in os.listdir(DEBUG_DIR):
            os.remove(os.path.join(DEBUG_DIR, f))

    # 1. Load failures into memory
    failed_items_queue = []
    with open(FAILED_LOG, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                failed_items_queue.append(data)
            except: pass
    
    total_files = len(failed_items_queue)
    print(f"Found {total_files} failed items to retry.")
    
    success_count = 0
    still_failed_records = []

    # 2. Open Success Output in Append Mode
    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f_out: 
        for idx, item in enumerate(failed_items_queue):
            filename = item['filename']
            filepath = os.path.join(IMG_DIR, filename)
            
            if DEBUG_PRINT:
                print(f"[{idx+1}/{total_files}] Retrying: {filename}")
            elif idx % 10 == 0:
                print(f"Retrying... [{idx}/{total_files}] | Fixed: {success_count}")

            result, is_valid, raw_data = run_ocr_on_image_file(filepath, filename)
            date_tag = extract_date_from_filename(filename)

            if is_valid:
                record = {
                    "filename": filename,
                    "date_tag": date_tag,
                    "status": "success",
                    "data": result
                }
                f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                success_count += 1
                
                if success_count % 5 == 0:
                    f_out.flush()
                    os.fsync(f_out.fileno())
            else:
                item['raw_data_dump'] = raw_data
                item['status'] = 'failed_retry_round2'
                still_failed_records.append(item)
            
            # --- STOP IF BOTH DEBUG MODES ON ---
            if DEBUG_PRINT and DEBUG_SAVE_IMAGES:
                 print("\n🛑 DEBUG STOP: Stopped after 1 file (Print+Save active).")
                 break

    # 3. OVERWRITE the failed log with only remaining failures
    # Only do this if we actually processed the whole list (didn't stop early for debug)
    if not (DEBUG_PRINT and DEBUG_SAVE_IMAGES):
        print(f"Overwriting {FAILED_LOG} with {len(still_failed_records)} remaining failures...")
        with open(FAILED_LOG, 'w', encoding='utf-8') as f_fail:
            for fail_item in still_failed_records:
                f_fail.write(json.dumps(fail_item, ensure_ascii=False) + "\n")
    else:
        print("⚠️ Debug Stop Active: 'ocr_failed_list.txt' was NOT updated to prevent data loss.")

    print("\n" + "="*40)
    print(f"Retry Complete")
    print(f"Fixed: {success_count}")
    print(f"Still Failing: {len(still_failed_records)}")
    print("="*40)

if __name__ == "__main__":
    main()