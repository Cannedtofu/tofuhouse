import cv2
import numpy as np
import os
from paddleocr import PaddleOCR
import json
import config

# Faster initialization
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

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

if __name__ == "__main__":
    extractor = ChageeOCRExtractor()
    # Test on test_1.png
    base_path = "d:/代码项目/Chagee applet/OCR_sample/"
    results = extractor.extract_data(os.path.join(base_path, "test_1.png"))
    print(json.dumps(results, indent=2, ensure_ascii=False))
