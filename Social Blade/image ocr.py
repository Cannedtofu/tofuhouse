import pyautogui
import pytesseract
import cv2
import numpy as np
from PIL import Image, ImageGrab
import time
import os
from datetime import datetime
import keyboard  # Global hotkey support
import csv

CSV_FILE = "tooltip_ocr_log.csv"
# Ensure CSV file has headers if new
if not os.path.isfile(CSV_FILE):
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Date", "Label", "Value"])


# === CONFIGURATION ===
CAPTURE_WIDTH = 400
CAPTURE_HEIGHT = 300
DELAY_SECONDS = 0.2  # Delay between scans
LOG_FILE = "tooltip_ocr_log.txt"
DEBUG_IMAGE = False  # Set to True to save debug box images

# OPTIONAL: Path to tesseract
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Ensure log file directory exists
os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)

def capture_area_around_cursor():
    x, y = pyautogui.position()
    bbox = (
        x - CAPTURE_WIDTH // 2,
        y - CAPTURE_HEIGHT // 2,
        x + CAPTURE_WIDTH // 2,
        y + CAPTURE_HEIGHT // 2
    )
    img = ImageGrab.grab(bbox)
    img.save("debug_mouse_area.png")
    return ImageGrab.grab(bbox)

DEBUG_IMAGE = True  # Toggle as needed

def find_tooltip_box(pil_image: Image.Image) -> Image.Image or None:
    img = np.array(pil_image.convert("RGB"))
    original = img.copy()
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape

    # Thresholding (you were using adaptive, but we’ll match your original method more closely)
    _, thresh = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY)
    # Alternatively, replace with adaptive if 232 doesn’t generalize well

    # Find external contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"Found {len(contours)} contours")

    candidates = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = cv2.contourArea(cnt)

        if area < 1000:
            continue

        if x <= 0 or y <= 0 or x + w >= width or y + h >= height:
            continue

        roi = img[y:y+h, x:x+w]
        mean_color = cv2.mean(roi)[:3]

        # Check if it's a rectangle
        approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
        if len(approx) != 4:
            continue

        # Light tooltip background
        if not all(channel > 210 for channel in mean_color):
            continue

        candidates.append((x, y, w, h))
        cv2.rectangle(original, (x, y), (x + w, y + h), (0, 255, 0), 2)

    if not candidates:
        return None

    # Choose the largest box
    x, y, w, h = max(candidates, key=lambda b: b[2] * b[3])
    cropped = img[y:y+h, x:x+w]


    if DEBUG_IMAGE:
        cv2.rectangle(original, (x, y), (x + w, y + h), (0, 0, 255), 3)
        debug_bgr = cv2.cvtColor(original, cv2.COLOR_RGB2BGR)
        cv2.imwrite("debug_boxes_formal.png", debug_bgr)
        cv2.imwrite("thresh_debug.png", thresh)
        # Save the cropped image
        cropped_image_pil = Image.fromarray(cropped)
        cropped_image_pil.save("cropped_tooltip.png")  # Save as PNG (or adjust the format)

    return Image.fromarray(cropped)

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def extract_text_from_image(img: Image.Image) -> str:
    # Convert the PIL image to a numpy array for OpenCV processing
    img_cv = np.array(img)
    
    # Convert the image to grayscale (Tesseract prefers grayscale input)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
    
    # Apply thresholding to increase contrast between text and background
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)  # Inverse binary threshold

    # Optionally apply denoising (removes small noise pixels)
    denoised = cv2.fastNlMeansDenoising(thresh, None, 30, 7, 21)

    # Convert back to PIL image format (if needed)
    processed_image = Image.fromarray(denoised)
    
    # Extract text using pytesseract
    return pytesseract.image_to_string(processed_image)

def main_loop():
    print("🔁 Running OCR mouse capture... Press ESC to stop.\n")
    while True:
        try:
            if keyboard.is_pressed("esc"):
                print("\n🛑 ESC pressed — stopping.")
                break

            full_img = capture_area_around_cursor()
            tooltip = find_tooltip_box(full_img)
            if tooltip:
                text = extract_text_from_image(tooltip).strip()
                if text:
                    lines = text.splitlines()
                    lines = [line.strip() for line in lines if line.strip()]
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    if len(lines) >= 2:
                        # Example input: "Jul 22, 2024", "@ Views 423,891"
                        date_line = lines[0]
                        data_line = lines[1].replace("@", "").strip()
                        parts = data_line.split()

                        if len(parts) >= 2:
                            label = parts[0]
                            value = " ".join(parts[1:])  # Allows for numbers like 423,891

                            print(f"|{timestamp}|{date_line}|{label}|{value}|")

                            # Append to CSV
                            with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                                writer = csv.writer(f)
                                writer.writerow([timestamp, date_line, label, value])
            else:
                print("(No tooltip detected)")

            time.sleep(DELAY_SECONDS)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main_loop()