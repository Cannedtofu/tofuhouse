import os
import datetime
import cv2
import pytesseract
from PIL import Image
from pytesseract import Output



# === CONFIG / TUNABLE PARAMETERS ==============================================

TESS_LANG = "chi_sim"
UPSCALE_FACTOR = 2.0
USE_THRESHOLD = True
THRESHOLD_EARLY = True
THRESHOLD_MODE = "OTSU"
THRESH_BINARY_VALUE = 180
ONLY_NUMBERS_AND_DOTS = False
TESSERACT_PSM = 6
ASSUMED_DPI = 300
SAVE_STEPS = False
OUTPUT_DIR = "ocr_tuning"
REMOVE_CENTER = True
CENTER_FRACTION = 1 / 3
SPLIT_LEFT_RIGHT = True

# Tesseract executable
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# =============================================================================

def ocr_image(img):
    # img: PIL Image
    # Use UTF-8 for decoding
    return pytesseract.image_to_string(img, lang=TESS_LANG, config=config, output_type=pytesseract.Output.STRING)


def split_left_right(img):
    h, w = img.shape[:2]
    mid = w // 2
    return img[:, :mid], img[:, mid:]

def _ensure_run_dir() -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(OUTPUT_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir

def _save_step(run_dir: str, name: str, img) -> str:
    if not SAVE_STEPS:
        return ""
    if not hasattr(_save_step, "counter"):
        _save_step.counter = 0
    _save_step.counter += 1
    filename = f"{_save_step.counter:02d}_{name}.png"
    path = os.path.join(run_dir, filename)
    ok = cv2.imwrite(path, img)
    if not ok:
        raise RuntimeError(f"Failed to write image: {path}")
    return path


def preprocess_and_ocr(image=None) -> str:
    """
    Main function: returns OCR text from an image.
    image: a NumPy array (BGR) or None. If None, raise error.
    """
    if image is None:
        raise ValueError("No image provided. Pass a NumPy array as 'image'.")

    # Reset step counter
    if hasattr(_save_step, "counter"):
        delattr(_save_step, "counter")

    # Prepare run directory
    run_dir = _ensure_run_dir()
    _save_step(run_dir, "original_bgr", image)

    # --- Remove center if needed ---
    if REMOVE_CENTER:
        h, w = image.shape[:2]
        cut_w = int(w * CENTER_FRACTION)
        cut_h = int(h * CENTER_FRACTION)
        cx, cy = w // 2, h // 2
        x1 = cx - cut_w // 2
        x2 = cx + cut_w // 2
        y1 = cy - cut_h // 2
        y2 = cy + cut_h // 2
        image[y1:y2, x1:x2] = 255
        _save_step(run_dir, "center_removed", image)

    # --- Grayscale ---
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _save_step(run_dir, "gray", gray)

    # --- Upscale ---
    if UPSCALE_FACTOR != 1.0:
        new_w = int(gray.shape[1] * UPSCALE_FACTOR)
        new_h = int(gray.shape[0] * UPSCALE_FACTOR)
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        _save_step(run_dir, f"upscaled_x{UPSCALE_FACTOR}", gray)

    processed = gray.copy()

    # --- Early threshold ---
    if USE_THRESHOLD and THRESHOLD_EARLY:
        if THRESHOLD_MODE == "BINARY":
            _, processed = cv2.threshold(gray, THRESH_BINARY_VALUE, 255, cv2.THRESH_BINARY)
        elif THRESHOLD_MODE == "OTSU":
            _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _save_step(run_dir, f"early_threshold_{THRESHOLD_MODE.lower()}", processed)

    # --- Ensure black text on white ---
    if cv2.countNonZero(processed) / processed.size < 0.5:
        processed = cv2.bitwise_not(processed)
        _save_step(run_dir, "inverted", processed)

    # --- Tesseract config ---
    config_parts = [f"--psm {TESSERACT_PSM}", f"--dpi {ASSUMED_DPI}"]
    if ONLY_NUMBERS_AND_DOTS:
        config_parts.append('-c tessedit_char_whitelist="0123456789.,+-/%"')
    config = " ".join(config_parts)

    # --- OCR ---
    if SPLIT_LEFT_RIGHT:
        left_img, right_img = split_left_right(processed)
        _save_step(run_dir, "left_part", left_img)
        _save_step(run_dir, "right_part", right_img)

        # Convert to PIL and get bytes
        left_bytes = pytesseract.image_to_string(Image.fromarray(left_img),
                                                lang=TESS_LANG,
                                                config=config,
                                                output_type=Output.BYTES)
        right_bytes = pytesseract.image_to_string(Image.fromarray(right_img),
                                                lang=TESS_LANG,
                                                config=config,
                                                output_type=Output.BYTES)
        # Decode explicitly as UTF-8
        text_left = left_bytes.decode("utf-8", errors="ignore")
        text_right = right_bytes.decode("utf-8", errors="ignore")
        text = text_left + "\n" + text_right
    else:
        raw_bytes = pytesseract.image_to_string(Image.fromarray(processed),
                                                lang=TESS_LANG,
                                                config=config,
                                                output_type=Output.BYTES)
        text = raw_bytes.decode("utf-8", errors="ignore")

    return text

def parse_ocr_to_image_result(ocr_text: str) -> dict:
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    result = {}
    for i in range(0, len(lines)-1, 2):
        category = lines[i].replace(" ", "")
        value = lines[i+1].replace(" ", "")
        result[category] = value
    return result

# img = cv2.imread("test.jpg")
# ocr_raw=preprocess_and_ocr(img)
# ocr_text = parse_ocr_to_image_result(ocr_raw)
# print(ocr_text)