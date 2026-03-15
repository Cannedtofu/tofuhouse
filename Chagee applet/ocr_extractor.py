import cv2
import numpy as np
import os
import pytesseract
import json

# Set tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

class ChageeOCRExtractor:
    def __init__(self, lang='chi_sim'):
        self.lang = lang
        self.custom_config = r'--oem 3 --psm 7' # PSM 7: Treat the image as a single text line.

    def _load_image(self, path):
        with open(path, 'rb') as f:
            img_array = np.frombuffer(f.read(), np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    def _preprocess_for_ocr(self, img):
        # Upscale
        img = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Simple thresholding after upscale - often cleaner for black on light
        thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1]
        return thresh

    def extract_data(self, image_path):
        img = self._load_image(image_path)
        if img is None:
            return []

        h_full, w_full = img.shape[:2]
        # Ignore top and bottom static elements
        roi = img[int(h_full * 0.15):int(h_full * 0.90), :]
        
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Use more standard edge detection
        edges = cv2.Canny(gray, 30, 80)
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=2) 
        
        # Use RETR_LIST to catch everything, then filter
        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Width ~388. User says height at least 50.
            if 350 <= w <= 440 and 50 <= h <= 400:
                boxes.append((x, y + int(h_full * 0.15), w, h))
        
        boxes.sort(key=lambda b: b[1])
        
        final_boxes = []
        for b in boxes:
            # deduplicate overlapping detections (same box found twice)
            is_overlap = False
            for fb in final_boxes:
                # If y coordinates are very close
                if abs(b[1] - fb[1]) < 30: # Tightened from 40 to 30
                    is_overlap = True
                    break
            if not is_overlap:
                final_boxes.append(b)
        
        print(f"Detected {len(final_boxes)} boxes in {os.path.basename(image_path)}")
        
        extracted_data = []
        debug_base = os.path.join(os.path.dirname(image_path), f"debug_{os.path.basename(image_path)}")
        if not os.path.exists(debug_base):
            os.makedirs(debug_base)

        for i, (bx, by, bw, bh) in enumerate(final_boxes):
            box_img = img[by:by + bh, bx:bx + bw]
            self._save_image(os.path.join(debug_base, f"box_{i}_full.png"), box_img)
            
            # User Rule: Crop top 20px, keep next 20px (+1 extra at bottom)
            # Expanded: add 1px on top (23->22), 2px at bottom (21+1+2=24)
            sn_y_start = 22 
            sn_height = 24 
            # Left 75% to avoid distance text
            sn_roi = box_img[sn_y_start : sn_y_start + sn_height, 10 : int(bw * 0.75)]
            
            # User Rule: Order status crop top by 2, add 2 to bottom
            os_y_start = sn_y_start + sn_height + 2
            os_height = 24 
            os_roi = box_img[os_y_start : os_y_start + os_height, 10 : int(bw * 0.75)]
            
            # Log for inspection as requested
            self._save_image(os.path.join(debug_base, f"box_{i}_sn_crop.png"), sn_roi)
            self._save_image(os.path.join(debug_base, f"box_{i}_os_crop.png"), os_roi)
            
            # OCR part (still there but we focus on crops)
            sn_proc = self._preprocess_for_ocr(sn_roi)
            os_proc = self._preprocess_for_ocr(os_roi)
            
            config = r'--oem 3 --psm 7'
            store_name_raw = pytesseract.image_to_string(sn_proc, lang=self.lang, config=config).strip()
            order_status_raw = pytesseract.image_to_string(os_proc, lang=self.lang, config=config).strip()
            
            def clean_text(text):
                text = "".join([c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum()])
                replacements = {'卜消': '上海', '一浪': '上海', '一津': '上海', '广海': '上海', '门海': '上海', '一街': '上海', '卜海': '上海'}
                for k, v in replacements.items(): text = text.replace(k, v)
                return text.strip()

            store_name = clean_text(store_name_raw)
            
            def fuzzy_match_status(raw):
                normalized = raw.replace('卵', '即').replace('刻', '制').replace('佛', '作').replace('下羡', '下单').replace('析', '6')
                if any(k in normalized for k in ['立即制作', '现在下单', '立即', '下单', '制作中']):
                    if '制作中' in normalized:
                        import re
                        digits = re.findall(r'\d+', normalized)
                        if digits: return f"前方{digits[0]}杯制作中"
                    return "现在下单，立即制作"
                return clean_text(normalized)

            order_status = fuzzy_match_status(order_status_raw)
            
            extracted_data.append({
                "store_name": store_name,
                "order_status": order_status,
                "debug_sn": os.path.join(debug_base, f"box_{i}_sn_crop.png"),
                "debug_os": os.path.join(debug_base, f"box_{i}_os_crop.png")
            })
        
        return extracted_data

    def _save_image(self, path, img):
        _, ext = os.path.splitext(path)
        res, img_encode = cv2.imencode(ext, img)
        if res:
            with open(path, 'wb') as f:
                f.write(img_encode)

if __name__ == "__main__":
    extractor = ChageeOCRExtractor()
    # Test on test_1.png
    base_path = "d:/代码项目/Chagee applet/OCR_sample/"
    results = extractor.extract_data(os.path.join(base_path, "test_1.png"))
    print(json.dumps(results, indent=2, ensure_ascii=False))
