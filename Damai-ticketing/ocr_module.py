import cv2
import pytesseract
import numpy as np
from PIL import Image
from pytesseract import Output

# === CONFIG / TUNABLE PARAMETERS ==========================================
TESS_LANG = "chi_sim"
UPSCALE_FACTOR = 2.0
USE_THRESHOLD = True
THRESHOLD_MODE = "OTSU"  # or "BINARY"
THRESH_BINARY_VALUE = 180
ONLY_NUMBERS_AND_DOTS = False
TESSERACT_PSM = 6
ASSUMED_DPI = 300
SPLIT_LEFT_RIGHT = True
REMOVE_CENTER = True
CENTER_FRACTION = 1/3

# Tesseract executable
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ==========================================================================

def _split_left_right(img: np.ndarray):
    h, w = img.shape[:2]
    mid = w // 2
    return img[:, :mid], img[:, mid:]

def preprocess_and_ocr(image: np.ndarray) -> str:
    """
    Production-ready OCR function.
    Accepts: NumPy array (BGR) from screenshot
    Returns: OCR text as string
    """
    if image is None:
        raise ValueError("No image provided.")

    img = image.copy()

    # --- Remove center region if needed ---
    if REMOVE_CENTER:
        h, w = img.shape[:2]
        cx, cy = w // 2, h // 2
        cut_w, cut_h = int(w * CENTER_FRACTION), int(h * CENTER_FRACTION)
        x1, x2 = cx - cut_w // 2, cx + cut_w // 2
        y1, y2 = cy - cut_h // 2, cy + cut_h // 2
        img[y1:y2, x1:x2] = 255  # white-out

    # --- Grayscale ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # --- Upscale ---
    if UPSCALE_FACTOR != 1.0:
        new_w, new_h = int(gray.shape[1]*UPSCALE_FACTOR), int(gray.shape[0]*UPSCALE_FACTOR)
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    # --- Threshold ---
    processed = gray.copy()
    if USE_THRESHOLD:
        if THRESHOLD_MODE == "BINARY":
            _, processed = cv2.threshold(processed, THRESH_BINARY_VALUE, 255, cv2.THRESH_BINARY)
        elif THRESHOLD_MODE == "OTSU":
            _, processed = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # --- Ensure black text on white ---
    if cv2.countNonZero(processed) / processed.size < 0.5:
        processed = cv2.bitwise_not(processed)

    # --- Tesseract config ---
    config_parts = [f"--psm {TESSERACT_PSM}", f"--dpi {ASSUMED_DPI}"]
    if ONLY_NUMBERS_AND_DOTS:
        config_parts.append('-c tessedit_char_whitelist="0123456789.,+-/%"')
    config = " ".join(config_parts)

    # --- OCR ---
    if SPLIT_LEFT_RIGHT:
        left_img, right_img = _split_left_right(processed)
        text_left = pytesseract.image_to_string(Image.fromarray(left_img), lang=TESS_LANG, config=config)
        text_right = pytesseract.image_to_string(Image.fromarray(right_img), lang=TESS_LANG, config=config)
        text = text_left + "\n" + text_right
    else:
        text = pytesseract.image_to_string(Image.fromarray(processed), lang=TESS_LANG, config=config)

    return text

def parse_ocr_to_image_result(ocr_text: str) -> dict:
    """Convert raw OCR text into key-value dict (lines in pairs)."""
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    result = {}
    for i in range(0, len(lines)-1, 2):
        key = lines[i].replace(" ", "")
        value = lines[i+1].replace(" ", "")
        result[key] = value
    return result
