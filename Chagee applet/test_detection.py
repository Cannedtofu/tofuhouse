import cv2
import numpy as np
import os
import pytesseract

# Set tesseract path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def save_image(path, img):
    _, ext = os.path.splitext(path)
    res, img_encode = cv2.imencode(ext, img)
    if res:
        with open(path, 'wb') as f:
            f.write(img_encode)

def detect_boxes(image_path, output_dir):
    # Use numpy to read the file to handle non-ascii paths
    with open(image_path, 'rb') as f:
        img_array = np.frombuffer(f.read(), np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    if img is None:
        print(f"Failed to load {image_path}")
        return
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Try edges + dilation to close boxes
    edges = cv2.Canny(gray, 50, 150)
    kernel = np.ones((5,5), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=1)
    
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    boxes = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if 350 <= w <= 450 and h >= 100:
            boxes.append((x, y, w, h))
            
    boxes.sort(key=lambda b: b[1])
    
    final_boxes = []
    for b in boxes:
        if not final_boxes or b[1] - final_boxes[-1][1] > 50:
            final_boxes.append(b)
    
    print(f"Found {len(final_boxes)} boxes in {image_path}")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    results = []
    for i, (x, y, w, h) in enumerate(final_boxes):
        box_img = img[y:y+h, x:x+w]
        save_image(os.path.join(output_dir, f"box_{i}.png"), box_img)
        
        # Extract store_name region (20-40px from top)
        sn_y1, sn_y2 = 18, 55
        store_name_img = box_img[sn_y1:sn_y2, 15:w-10]
        save_image(os.path.join(output_dir, f"box_{i}_store_name.png"), store_name_img)
        
        # Extract order_status region
        os_y1, os_y2 = 55, 88
        order_status_img = box_img[os_y1:os_y2, 15:w-10]
        save_image(os.path.join(output_dir, f"box_{i}_order_status.png"), order_status_img)
        
        # Preprocess for OCR: Upscale and Threshold
        def preprocess_for_ocr(img):
            # Upscale 2x
            img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # Thresholding
            thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            return thresh

        sn_proc = preprocess_for_ocr(store_name_img)
        os_proc = preprocess_for_ocr(order_status_img)
        
        # save_image(os.path.join(output_dir, f"box_{i}_sn_proc.png"), sn_proc)
        # save_image(os.path.join(output_dir, f"box_{i}_os_proc.png"), os_proc)

        # OCR
        custom_config = r'--oem 3 --psm 7'
        store_name = pytesseract.image_to_string(sn_proc, lang='chi_sim', config=custom_config).strip()
        order_status = pytesseract.image_to_string(os_proc, lang='chi_sim', config=custom_config).strip()
        
        print(f"Box {i}: Store='{store_name}', Status='{order_status}'")
        results.append((store_name, order_status))
    
    return results

if __name__ == "__main__":
    detect_boxes("d:/代码项目/Chagee applet/OCR_sample/test_1.png", "d:/代码项目/Chagee applet/OCR_sample/debug_test_1")
