import os
import datetime

import cv2
import pytesseract

# If Tesseract is not in PATH, uncomment and set this:
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# === TUNABLE PARAMETERS (feel free to change these) ============================================

# Path to the image you want to OCR (relative to where you run this script)
IMAGE_PATH = "test.jpg"  # <-- change if your image is elsewhere

# Tesseract language packs; chi_sim is for simplified Chinese
TESS_LANG = "chi_sim"  # <-- add/remove languages here (e.g. "chi_sim", "chi_sim+eng")

# Overall scaling before OCR (helps on small images)
UPSCALE_FACTOR = 2.0  # <-- try 1.0, 1.5, 2.0, 3.0

# Blur / denoise options
USE_GAUSSIAN_BLUR = True   # <-- toggle on/off
GAUSSIAN_KERNEL = (3, 3)   # <-- try (3,3), (5,5)

USE_BILATERAL_FILTER = False  # <-- toggle on/off

# Thresholding options
USE_THRESHOLD = True            # <-- toggle on/off
THRESHOLD_MODE = "OTSU"         # <-- "OTSU", "BINARY", "ADAPTIVE_MEAN", "ADAPTIVE_GAUSSIAN"
THRESH_BINARY_VALUE = 180       # <-- used when THRESHOLD_MODE == "BINARY"
ADAPTIVE_BLOCK_SIZE = 31        # <-- odd number, e.g. 11, 21, 31
ADAPTIVE_C = 10                 # <-- small integer, tweak contrast

# OCR behavior
ONLY_NUMBERS_AND_DOTS = False   # <-- True = restrict to digits and decimal punctuation
TESSERACT_PSM = 6               # <-- page segmentation mode (3, 6, 7, 11, 13 are common)
ASSUMED_DPI = 300               # <-- reported dpi to Tesseract

# Saving intermediate images
SAVE_STEPS = True               # <-- toggle saving of every intermediate image
OUTPUT_DIR = "ocr_tuning"       # <-- directory where step images go

# ==============================================================================================


def _ensure_run_dir() -> str:
    """Create a timestamped directory to store this run's intermediate images."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(OUTPUT_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def _save_step(run_dir: str, name: str, img) -> str:
    """Save an intermediate image (increments step counter each time)."""
    if not SAVE_STEPS:
        return ""

    if not hasattr(_save_step, "counter"):
        _save_step.counter = 0  # type: ignore[attr-defined]

    _save_step.counter += 1  # type: ignore[attr-defined]
    filename = f"{_save_step.counter:02d}_{name}.png"  # type: ignore[attr-defined]
    path = os.path.join(run_dir, filename)
    cv2.imwrite(path, img)
    return path


def preprocess_and_ocr():
    # Load the original image
    image = cv2.imread(IMAGE_PATH)
    if image is None:
        raise FileNotFoundError(f"Could not read image at '{IMAGE_PATH}'.")

    run_dir = _ensure_run_dir()
    print(f"Saving intermediate images to: {run_dir}")

    _save_step(run_dir, "original_bgr", image)

    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _save_step(run_dir, "gray", gray)

    # Upscale (if factor != 1.0)
    if UPSCALE_FACTOR and UPSCALE_FACTOR != 1.0:
        new_w = int(gray.shape[1] * UPSCALE_FACTOR)
        new_h = int(gray.shape[0] * UPSCALE_FACTOR)
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        _save_step(run_dir, f"upscaled_x{UPSCALE_FACTOR}", gray)

    # Optional denoising / blurring
    if USE_GAUSSIAN_BLUR:
        gray = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, 0)
        _save_step(run_dir, f"gaussian_blur_{GAUSSIAN_KERNEL[0]}x{GAUSSIAN_KERNEL[1]}", gray)

    if USE_BILATERAL_FILTER:
        gray = cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)
        _save_step(run_dir, "bilateral_filter", gray)

    processed = gray

    # Thresholding
    if USE_THRESHOLD:
        if THRESHOLD_MODE == "BINARY":
            _, processed = cv2.threshold(
                gray, THRESH_BINARY_VALUE, 255, cv2.THRESH_BINARY
            )
        elif THRESHOLD_MODE == "OTSU":
            _, processed = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
        elif THRESHOLD_MODE == "ADAPTIVE_MEAN":
            processed = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY,
                ADAPTIVE_BLOCK_SIZE,
                ADAPTIVE_C,
            )
        elif THRESHOLD_MODE == "ADAPTIVE_GAUSSIAN":
            processed = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                ADAPTIVE_BLOCK_SIZE,
                ADAPTIVE_C,
            )
        else:
            raise ValueError(f"Unknown THRESHOLD_MODE: {THRESHOLD_MODE}")

        _save_step(run_dir, f"threshold_{THRESHOLD_MODE.lower()}", processed)

    # Build Tesseract config
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

    # Run OCR
    text = pytesseract.image_to_string(processed, lang=TESS_LANG, config=config)

    print("\n===== OCR RESULT =====")
    print(text)
    print("======================\n")


if __name__ == "__main__":
    preprocess_and_ocr()


