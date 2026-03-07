"""
OCR helper — find a target text string within a screenshot and return its
centre pixel coordinates.

Supports both English and Chinese (Simplified) text via EasyOCR.

Usage
-----
    from ocr import find_text_in_image

    coords = find_text_in_image("output/before_tap.png", "玩具系列")
    if coords:
        x, y = coords
        driver.tap([(x, y)])
    else:
        print("Text not found in image.")

Standalone demo:
    python ocr.py output/before_tap.png 玩具系列
"""

from __future__ import annotations

import logging
import sys
from difflib import SequenceMatcher
from typing import Optional

import easyocr

logger = logging.getLogger(__name__)

# Lazy-initialised reader — model load is slow (~3 s first time), so we keep
# a single shared instance for the lifetime of the process.
_reader: Optional[easyocr.Reader] = None


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        logger.info("Loading EasyOCR model (first run downloads ~200 MB) …")
        # gpu=False ensures it works on machines without CUDA.
        # Add "en" alongside "ch_sim" so mixed Chinese/English labels work.
        _reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        logger.info("EasyOCR model loaded.")
    return _reader


# ── Public API ────────────────────────────────────────────────────────────────

def find_text_in_image(
    image_path: str,
    target_text: str,
    threshold: float = 0.6,
) -> Optional[tuple[int, int]]:
    """
    Run OCR on *image_path* and return the centre pixel (x, y) of the
    element whose recognised text best matches *target_text*.

    Matching strategy (tried in order):
      1. Exact substring match — case-insensitive, full-width/half-width aware
      2. Highest SequenceMatcher similarity score among all detected tokens

    Parameters
    ----------
    image_path  : path to a PNG/JPG screenshot
    target_text : the text you want to locate (English or Chinese)
    threshold   : minimum similarity score for the fuzzy-match fallback
                  (0 = accept anything, 1.0 = exact only).  Default 0.6.

    Returns
    -------
    (x, y) centre pixel of the best matching text box, or None if not found.
    """
    reader = _get_reader()

    # readtext returns: list of ([tl, tr, br, bl], text, confidence)
    results = reader.readtext(image_path, detail=1)

    if not results:
        logger.warning("OCR returned no text from %s", image_path)
        return None

    target_norm = _normalise(target_text)
    best_box: Optional[list] = None
    best_score: float = -1.0
    best_text: str = ""

    for (bbox, text, _conf) in results:
        text_norm = _normalise(text)

        # Strategy 1: exact substring (target inside OCR token, or vice-versa)
        if target_norm in text_norm or text_norm in target_norm:
            cx, cy = _bbox_center(bbox)
            logger.info("Exact match  %-20r → (%d, %d)", text, cx, cy)
            return cx, cy

        # Strategy 2: fuzzy similarity
        score = SequenceMatcher(None, target_norm, text_norm).ratio()
        if score > best_score:
            best_score = score
            best_box = bbox
            best_text = text

    if best_box is not None and best_score >= threshold:
        cx, cy = _bbox_center(best_box)
        logger.info(
            "Fuzzy match  %-20r (score=%.2f) → (%d, %d)",
            best_text, best_score, cx, cy,
        )
        return cx, cy

    logger.warning(
        "No match found for %r (best=%.2f, threshold=%.2f)",
        target_text, best_score, threshold,
    )
    return None


def ocr_all_text(image_path: str) -> list[dict]:
    """
    Return every detected text block in *image_path* as a list of dicts:
        {"text": str, "center": (x, y), "confidence": float, "bbox": [...]}

    Useful for debugging — call this to see everything OCR can see in a
    screenshot before you call find_text_in_image.
    """
    reader = _get_reader()
    results = reader.readtext(image_path, detail=1)
    out = []
    for (bbox, text, conf) in results:
        cx, cy = _bbox_center(bbox)
        out.append({"text": text, "center": (cx, cy), "confidence": conf, "bbox": bbox})
    return out


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    """Lowercase + strip whitespace.  Full-width → half-width for digits/ASCII."""
    s = s.strip().lower()
    # convert full-width ASCII (！ → !, ａ → a, etc.)
    result = []
    for ch in s:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _bbox_center(bbox) -> tuple[int, int]:
    """
    Compute centre pixel from EasyOCR's 4-point bounding box.
    bbox: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]  (top-left, clockwise)
    """
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return int(sum(xs) / 4), int(sum(ys) / 4)


# ── Standalone demo ───────────────────────────────────────────────────────────

def _demo(image_path: str, target_text: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    print(f"\nImage  : {image_path}")
    print(f"Target : {target_text!r}\n")

    print("── All detected text ──────────────────────────────────────────────")
    blocks = ocr_all_text(image_path)
    if not blocks:
        print("  (none)")
    for b in blocks:
        print(f"  ({b['center'][0]:4d}, {b['center'][1]:4d})  conf={b['confidence']:.2f}  {b['text']!r}")

    print("\n── Search result ───────────────────────────────────────────────────")
    coords = find_text_in_image(image_path, target_text)
    if coords:
        print(f"  Found at pixel ({coords[0]}, {coords[1]})")
    else:
        print("  Not found.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python ocr.py <image_path> <target_text>")
        print("Example: python ocr.py output/before_tap.png 玩具系列")
        sys.exit(1)
    _demo(sys.argv[1], sys.argv[2])
