import cv2
from paddleocr import PaddleOCR
import numpy as np

def main():
    img_path = r"d:\代码项目\Chagee applet\store_sku.png"
    ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False, use_gpu=False)
    
    # Read image
    with open(img_path, 'rb') as f:
        img_array = np.frombuffer(f.read(), np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    print(f"Image shape: {img.shape}")
    
    # Let's crop it with the logic from get_store_sku.py
    roi_x1, roi_y1 = 93, 224
    roi_x2, roi_y2 = 406, 644
    
    h_full, w_full = img.shape[:2]
    x1, x2 = max(0, roi_x1), min(w_full, roi_x2)
    y1, y2 = max(0, roi_y1), min(h_full, roi_y2)
    
    crop_img = img[y1:y2, x1:x2]
    print(f"Crop shape: {crop_img.shape}")
    
    res = ocr.ocr(crop_img, det=True, cls=True)
    
    output = []
    page_data = []
    if res and res[0]:
        for box, (text, score) in res[0]:
            cy = (box[0][1] + box[2][1]) / 2.0
            cx = (box[0][0] + box[2][0]) / 2.0
            h = max(box[2][1], box[3][1]) - min(box[0][1], box[1][1])
            page_data.append({'text': text, 'cy': cy, 'cx': cx, 'h': h})
            output.append(f"Text: '{text}', cx: {cx:.2f}, cy: {cy:.2f}, h: {h:.2f}")

    prices = [b for b in page_data if '￥' in b['text'] or '¥' in b['text']]
    ignore_words = ['选规格', '加购', '去结算']
    texts = [b for b in page_data if '￥' not in b['text'] and '¥' not in b['text'] and not any(w in b['text'] for w in ignore_words)]

    LEFT_COL_CX = 80
    OPTIMAL_THRESHOLD = 17.5
    MIN_FALLBACK_HEIGHT = 16
    
    paired_results = []
    for pb in prices:
        candidates = [t for t in texts if t['cy'] < pb['cy'] and t['cx'] > LEFT_COL_CX]
        if not candidates:
            continue

        filtered = [t for t in candidates if t['h'] >= OPTIMAL_THRESHOLD]
        if filtered:
            filtered.sort(key=lambda t: pb['cy'] - t['cy'])
            store_sku = filtered[0]['text']
        else:
            candidates.sort(key=lambda t: -t['h'])
            best = candidates[0]
            if best['h'] < MIN_FALLBACK_HEIGHT:
                continue
            store_sku = best['text']

        if len(store_sku) > 12:
            continue

        clean_price = pb['text'].replace('起', '').strip()
        clean_price = clean_price.replace('￥', '').replace('¥', '')
        paired_results.append(f"{store_sku}, {clean_price}")

    output.append("\nFinal Extracted Pairs:")
    for pair in paired_results:
        output.append(pair)

    with open("test_ocr_results.txt", "w", encoding="utf-8") as out_f:
        out_f.write("\n".join(output))

if __name__ == '__main__':
    main()
