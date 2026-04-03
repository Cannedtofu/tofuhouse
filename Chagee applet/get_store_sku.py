import uiautomation as auto
import time
import os
import cv2
import numpy as np
from paddleocr import PaddleOCR

def get_applet_window():
    print("Looking for Chagee applet window...")
    for _ in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                print(f"Found applet window: {window.Name}")
                return window
        time.sleep(1)
    return None

def main():
    applet = get_applet_window()
    if not applet:
        print("Error: Applet not found.")
        return
        
    print("Focusing applet...")
    applet.SetActive()
    time.sleep(1)
    
    rect = applet.BoundingRectangle
    
    # Move mouse relative to the window
    mouse_x = rect.left + 410
    mouse_y = rect.top + 535
    print(f"Moving mouse to ({mouse_x}, {mouse_y})")
    auto.MoveTo(mouse_x, mouse_y)
    time.sleep(0.5)

    print("Initializing PaddleOCR...")
    ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
    
    # Menu crop region (relative to the window top-left)
    roi_x1, roi_y1 = 93, 224
    roi_x2, roi_y2 = 406, 644
    
    print("Resetting scroll to the top of the menu...")
    auto.MoveTo(mouse_x, mouse_y)
    auto.WheelUp(wheelTimes=20, interval=0.05)
    time.sleep(2)
    
    extracted_pages = []
    consecutive_no_new = 0
    max_no_new_scrolls = 3
    
    print("\nStarting extraction loop...")
    while consecutive_no_new < max_no_new_scrolls:
        temp_img_path = "temp_menu_full.png"
        applet.CaptureToImage(temp_img_path)
        
        with open(temp_img_path, 'rb') as f:
            img_array = np.frombuffer(f.read(), np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        
        h_full, w_full = img.shape[:2]
        x1, x2 = max(0, roi_x1), min(w_full, roi_x2)
        y1, y2 = max(0, roi_y1), min(h_full, roi_y2)
        
        crop_img = img[y1:y2, x1:x2]
        
        print("Running OCR on crop...")
        res = ocr.ocr(crop_img, det=True, cls=True)
        
        new_items = False
        if res and res[0]:
            boxes = res[0]
            # Convert to convenient dict
            page_data = []
            for box, (text, score) in boxes:
                text = text.strip()
                if not text: continue
                cy = (box[0][1] + box[2][1]) / 2.0
                cx = (box[0][0] + box[2][0]) / 2.0
                h = max(box[2][1], box[3][1]) - min(box[0][1], box[1][1])
                page_data.append({'text': text, 'cy': cy, 'cx': cx, 'h': h})
            
            # Simple check if this page is identical to the last one to detect end of scroll
            if extracted_pages and extracted_pages[-1] == page_data:
                pass
            else:
                extracted_pages.append(page_data)
                new_items = True

        if not new_items:
            consecutive_no_new += 1
            print(f"No new items found. Retry {consecutive_no_new}/{max_no_new_scrolls}.")
        else:
            consecutive_no_new = 0
            
        if consecutive_no_new >= max_no_new_scrolls:
            print("Reached bottom of the menu.")
            break
            
        print("Scrolling down...")
        auto.MoveTo(mouse_x, mouse_y)
        auto.WheelDown(wheelTimes=3, interval=0.1)
        time.sleep(2)
        
    print("\n--- Phase 2: Processing and Pairing SKUs ---")
    
    # Based on offline analysis, 20.0 is the optimal threshold to filter out description text
    # while keeping the larger SKU titles.
    OPTIMAL_THRESHOLD = 21.0
    print(f"Using Optimal Font Size Threshold: {OPTIMAL_THRESHOLD}")
    
    final_raw_texts = []
    sku_set = set()
    
    for page_data in extracted_pages:
        prices = [b for b in page_data if '￥' in b['text']]
        # Ignore UI buttons that often share the same font size
        ignore_words = ['选规格', '加购', '去结算']
        texts = [b for b in page_data if '￥' not in b['text'] and not any(w in b['text'] for w in ignore_words)]
        
        # Left-side decorative column sits at cx ≈ 25; actual menu content starts ~cx > 80
        LEFT_COL_CX = 80
        MIN_FALLBACK_HEIGHT = 16  # below this, text is too small to be a SKU title

        for pb in prices:
            # Exclude the far-left decorative column to avoid false title matches
            candidates = [t for t in texts if t['cy'] < pb['cy'] and t['cx'] > LEFT_COL_CX]
            if not candidates:
                continue

            # Primary: among large-font candidates, take the closest one above the price
            filtered = [t for t in candidates if t['h'] >= OPTIMAL_THRESHOLD]
            if filtered:
                filtered.sort(key=lambda t: pb['cy'] - t['cy'])
                store_sku = filtered[0]['text']
            else:
                # Fallback: take the largest-font text (most title-like) above the price
                candidates.sort(key=lambda t: -t['h'])
                best = candidates[0]
                if best['h'] < MIN_FALLBACK_HEIGHT:
                    continue  # No credible SKU title visible (likely scrolled off)
                store_sku = best['text']

            if len(store_sku) > 12:
                continue

            clean_price = pb['text'].replace('起', '').strip()
            sku_key = f"{store_sku}_{clean_price}"

            if sku_key not in sku_set:
                sku_set.add(sku_key)
                final_raw_texts.append({'SKU': store_sku, 'Price': clean_price})
    
    print("\n--- Extraction Complete ---")
    print(f"Total unique SKUs found: {len(final_raw_texts)}")
    
    if final_raw_texts:
        import pandas as pd
        df = pd.DataFrame(final_raw_texts)
        output_file = "store_sku_list.csv"
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"Saved extracted SKUs to {output_file}")
        print("\nExtracted Output:")
        print(df.to_string())

if __name__ == '__main__':
    main()
