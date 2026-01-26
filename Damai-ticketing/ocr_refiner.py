import cv2
import pytesseract
import numpy as np
import os
import re
import json
import shutil
from PIL import Image

# === CONFIGURATION ===
IMG_DIR = "scraped_charts"
OUTPUT_FILE = "ocr_refined_data.jsonl"
FAILED_LOG = "ocr_failed_list.txt"

# === RUN MODES ===
DEBUG_PRINT = False       # Set to True for console logs
DEBUG_SAVE_IMAGES = False # Set to True to save debug images
DEBUG_DIR = "ocr_debug_frames"

# Tesseract Path
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# === VALIDATION RULES ===
VALID_KEYS = {"其他", "旅游演艺", "舞蹈", "综艺", "戏剧", "曲杂", "音乐"}

if DEBUG_SAVE_IMAGES and not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

def clean_key_text(text):
    """Removes all non-Chinese characters from the key."""
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
    except Exception as e:
        if DEBUG_PRINT: print(f"Debug Save Error: {e}")
    return False

def is_valid_value(val_str):
    if not val_str: return False
    # Regex: Numbers, dots, +, 万, 亿
    pattern = re.compile(r'^[0-9\.\+\万\亿]+$') 
    return bool(pattern.match(val_str))

def validate_result(ocr_dict):
    if not ocr_dict: return False
    has_valid_key = False
    for k, v in ocr_dict.items():
        clean_k = k.strip()
        clean_v = v.strip()
        if clean_k in VALID_KEYS:
            has_valid_key = True
        else:
            return False 
        if not is_valid_value(clean_v):
            return False
    return has_valid_key

# === PREPROCESSING STRATEGIES ===

def apply_strategy(img_bgr, strategy_id, debug_name_prefix=""):
    img = img_bgr.copy()
    h, w = img.shape[:2]
    
    strategy_name = "Unknown"
    processed = None

    # Strategy 0: Original (Center Cut + Otsu)
    if strategy_id == 0:
        strategy_name = "0_Original_CenterCut"
        center_fraction = 1/3
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * center_fraction), int(h * center_fraction)
        x1, x2 = cx - cut_w // 2, cx + cut_w // 2
        y1, y2 = cy - cut_h // 2, cy + cut_h // 2
        img[y1:y2, x1:x2] = 255 
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Strategy 1: Standard Otsu
    elif strategy_id == 1:
        strategy_name = "1_Std_Otsu"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Strategy 2: Simple Binary 127
    elif strategy_id == 2:
        strategy_name = "2_Binary_127"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 127, 255, cv2.THRESH_BINARY)

    # Strategy 3: Erosion
    elif strategy_id == 3:
        strategy_name = "3_Erode"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = np.ones((2,2), np.uint8)
        processed = cv2.erode(processed, kernel, iterations=1)

    # Strategy 4: Dilation
    elif strategy_id == 4:
        strategy_name = "4_Dilate"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = np.ones((2,2), np.uint8)
        processed = cv2.dilate(processed, kernel, iterations=1)

    # Strategy 5: High Contrast + Blur
    elif strategy_id == 5:
        strategy_name = "5_Blur_Otsu"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(processed, (5,5), 0)
        _, processed = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if processed is not None:
        if cv2.countNonZero(processed) / processed.size < 0.5:
            processed = cv2.bitwise_not(processed)

    return processed, strategy_name

# === CORE OCR FUNCTION ===

def run_ocr_on_image_file(filepath, filename_only):
    try:
        img_array = np.fromfile(filepath, np.uint8)
        img_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    except Exception as e:
        if DEBUG_PRINT: print(f"Error reading file {filepath}: {e}")
        return None, False, {}

    if img_bgr is None:
        if DEBUG_PRINT: print(f"❌ Failed to decode image: {filepath}")
        return None, False, {}

    # Store Strat 0 result specifically for fallback
    strat0_result = {}

    for i in range(6):
        processed_img, strat_name = apply_strategy(img_bgr, i, debug_name_prefix=filename_only)
        if processed_img is None: continue

        if DEBUG_SAVE_IMAGES:
            full_path = os.path.join(DEBUG_DIR, f"{filename_only}_S{i}_Full.png")
            save_image_cv2_unicode(full_path, processed_img)

        # Split Left/Right
        h, w = processed_img.shape
        mid = w // 2
        left_img = processed_img[:, :mid]
        right_img = processed_img[:, mid:]

        if DEBUG_SAVE_IMAGES:
            save_image_cv2_unicode(os.path.join(DEBUG_DIR, f"{filename_only}_S{i}_Left.png"), left_img)
            save_image_cv2_unicode(os.path.join(DEBUG_DIR, f"{filename_only}_S{i}_Right.png"), right_img)

        # OCR
        custom_config = r'--psm 6 --dpi 300' 
        txt_left = pytesseract.image_to_string(left_img, lang='chi_sim', config=custom_config)
        txt_right = pytesseract.image_to_string(right_img, lang='chi_sim', config=custom_config)
        
        # Parse Logic
        full_text = txt_left + "\n" + txt_right
        lines = [line.strip().replace(" ", "") for line in full_text.splitlines() if line.strip()]

        if DEBUG_PRINT:
            print(f"   [Strat {i}] Raw Lines: {lines}")
        
        result = {}
        for j in range(0, len(lines)-1, 2):
            raw_key = lines[j]
            raw_val = lines[j+1]
            
            cleaned_key = clean_key_text(raw_key)
            if cleaned_key:
                result[cleaned_key] = raw_val

        # --- CAPTURE STRATEGY 0 ---
        if i == 0:
            strat0_result = result
        # --------------------------

        if DEBUG_PRINT:
            print(f"      [Parsed Result Strat {i}]: {result}")

        if validate_result(result):
            if DEBUG_PRINT: print(f"   ✅ Strategy {i} Succeeded.")
            return result, True, strat0_result

    if DEBUG_PRINT: print(f"   ⚠️ All strategies failed validation.")
    
    # Return Strat 0 result as the "raw_data" to save
    return strat0_result, False, strat0_result

# === MAIN RUNNER ===

def main():
    if not os.path.exists(IMG_DIR):
        print(f"Directory {IMG_DIR} not found.")
        return

    if DEBUG_SAVE_IMAGES and os.path.exists(DEBUG_DIR):
        for f in os.listdir(DEBUG_DIR):
            os.remove(os.path.join(DEBUG_DIR, f))
    
    files = [f for f in os.listdir(IMG_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    total_files = len(files)
    success_count = 0
    failed_files = []
    
    print(f"Found {total_files} images. Starting Re-OCR process...")
    print("-----------------------------------------------------")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f_out:
        with open(FAILED_LOG, 'w', encoding='utf-8') as f_fail:
            
            for idx, filename in enumerate(files):
                filepath = os.path.join(IMG_DIR, filename)
                
                if idx % 50 == 0:
                    print(f"Processing... [{idx}/{total_files}] | Success: {success_count} | Failed: {len(failed_files)}")

                if DEBUG_PRINT:
                    print(f"[{idx+1}/{total_files}] Processing: {filename}")

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
                    
                    if success_count % 10 == 0:
                        f_out.flush()
                        os.fsync(f_out.fileno())

                else:
                    fail_record = {
                        "filename": filename,
                        "date_tag": date_tag,
                        "status": "failed_validation",
                        "raw_data_dump": raw_data # <--- This is now strictly Strategy 0 result
                    }
                    f_fail.write(json.dumps(fail_record, ensure_ascii=False) + "\n")
                    
                    if len(failed_files) % 10 == 0:
                         f_fail.flush()
                         os.fsync(f_fail.fileno())
                         
                    failed_files.append(filename)
                
                if DEBUG_PRINT and DEBUG_SAVE_IMAGES:
                    print("\n🛑 DEBUG STOP: Stopped after 1 file (Print+Save active).")
                    break 

    print("\n" + "="*40)
    print(f"Re-OCR Complete")
    print(f"Total Processed: {total_files}")
    print(f"Success: {success_count}")
    print(f"Failed: {len(failed_files)}")
    print(f"Data saved to: {OUTPUT_FILE}")
    print("="*40)

if __name__ == "__main__":
    main()