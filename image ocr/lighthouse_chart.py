import os
import datetime

import cv2
import pytesseract

# If Tesseract is not in PATH, uncomment and set this:
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# === TUNABLE PARAMETERS ========================================================

IMAGE_PATH = "test.jpg"

TESS_LANG = "chi_sim"

UPSCALE_FACTOR = 2.0

# Thresholding behavior
USE_THRESHOLD = True
THRESHOLD_EARLY = True          # <<< EARLY binary right after grayscale
THRESHOLD_MODE = "OTSU"         # "OTSU" or "BINARY"
THRESH_BINARY_VALUE = 128

# Blur / denoise (used ONLY if not early-thresholding)
USE_GAUSSIAN_BLUR = True
GAUSSIAN_KERNEL = (3, 3)

USE_BILATERAL_FILTER = False

# OCR behavior
ONLY_NUMBERS_AND_DOTS = False
TESSERACT_PSM = 11
ASSUMED_DPI = 300

# Saving intermediate images
SAVE_STEPS = True
OUTPUT_DIR = "ocr_tuning"

REMOVE_CENTER = True
CENTER_FRACTION = 1 / 3

SPLIT_LEFT_RIGHT = True  # Split processed image into left/right after preprocessing

# =============================================================================

def split_left_right(img):
    h, w = img.shape[:2]
    mid = w // 2
    left = img[:, :mid]
    right = img[:, mid:]
    return left, right


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


def preprocess_and_ocr():
    # Reset step counter per run
    if hasattr(_save_step, "counter"):
        delattr(_save_step, "counter")

    image = cv2.imread(IMAGE_PATH)
    if image is None:
        raise FileNotFoundError(f"Could not read image at '{IMAGE_PATH}'.")



    run_dir = _ensure_run_dir()
    print("CWD:", os.getcwd())
    print("Saving intermediate images to:", run_dir)

    _save_step(run_dir, "original_bgr", image)

    # --- Center Crop ---
    if REMOVE_CENTER:
        h, w = image.shape[:2]

        cut_w = int(w * CENTER_FRACTION)
        cut_h = int(h * CENTER_FRACTION)

        cx, cy = w // 2, h // 2

        x1 = cx - cut_w // 2
        x2 = cx + cut_w // 2
        y1 = cy - cut_h // 2
        y2 = cy + cut_h // 2

        image[y1:y2, x1:x2] = 255  # white out center

        _save_step(run_dir, "center_removed", image)

    # --- Grayscale ---
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _save_step(run_dir, "gray", gray)

    # --- Upscale ---
    if UPSCALE_FACTOR and UPSCALE_FACTOR != 1.0:
        new_w = int(gray.shape[1] * UPSCALE_FACTOR)
        new_h = int(gray.shape[0] * UPSCALE_FACTOR)
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        _save_step(run_dir, f"upscaled_x{UPSCALE_FACTOR}", gray)

    processed = gray

    # === EARLY THRESHOLDING =====================================================
    if USE_THRESHOLD and THRESHOLD_EARLY:
        if THRESHOLD_MODE == "BINARY":
            _, processed = cv2.threshold(
                gray, THRESH_BINARY_VALUE, 255, cv2.THRESH_BINARY
            )
        elif THRESHOLD_MODE == "OTSU":
            _, processed = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
        else:
            raise ValueError("Early thresholding supports only BINARY or OTSU")

        _save_step(run_dir, f"early_threshold_{THRESHOLD_MODE.lower()}", processed)

    # === DENOISING / BLUR (ONLY if NOT early-thresholded) =======================
    if not THRESHOLD_EARLY:
        if USE_GAUSSIAN_BLUR:
            processed = cv2.GaussianBlur(processed, GAUSSIAN_KERNEL, 0)
            _save_step(
                run_dir,
                f"gaussian_blur_{GAUSSIAN_KERNEL[0]}x{GAUSSIAN_KERNEL[1]}",
                processed,
            )

        if USE_BILATERAL_FILTER:
            processed = cv2.bilateralFilter(
                processed, d=9, sigmaColor=75, sigmaSpace=75
            )
            _save_step(run_dir, "bilateral_filter", processed)

    # === LATE THRESHOLDING (disabled when early is used) ========================
    if USE_THRESHOLD and not THRESHOLD_EARLY:
        if THRESHOLD_MODE == "BINARY":
            _, processed = cv2.threshold(
                processed, THRESH_BINARY_VALUE, 255, cv2.THRESH_BINARY
            )
        elif THRESHOLD_MODE == "OTSU":
            _, processed = cv2.threshold(
                processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
        else:
            raise ValueError(f"Unknown THRESHOLD_MODE: {THRESHOLD_MODE}")

        _save_step(run_dir, f"threshold_{THRESHOLD_MODE.lower()}")

    # --- Ensure black text on white background ---
    white_ratio = cv2.countNonZero(processed) / processed.size
    if white_ratio < 0.5:
        processed = cv2.bitwise_not(processed)
        _save_step(run_dir, "inverted", processed)

    # --- Build Tesseract config ---
    config_parts = [
        f"--psm {TESSERACT_PSM}",
        f"--dpi {ASSUMED_DPI}",
    ]

    if ONLY_NUMBERS_AND_DOTS:
        whitelist = "0123456789.,+-/%"
        config_parts.append(f'-c tessedit_char_whitelist="{whitelist}"')

    config = " ".join(config_parts)

    print("Tesseract config:", config)
    print("Tesseract lang:  ", TESS_LANG)

    # --- OCR ---
    if SPLIT_LEFT_RIGHT:
        left_img, right_img = split_left_right(processed)

        _save_step(run_dir, "left_part", left_img)
        _save_step(run_dir, "right_part", right_img)

        text_left = pytesseract.image_to_string(left_img, lang=TESS_LANG, config=config)
        text_right = pytesseract.image_to_string(right_img, lang=TESS_LANG, config=config)

        text = text_left + "\n" + text_right  # Combine in reading order
    else:
        text = pytesseract.image_to_string(processed, lang=TESS_LANG, config=config)

    print("\n===== OCR RESULT =====")
    print(text)
    print("======================\n")


if __name__ == "__main__":
    preprocess_and_ocr()
