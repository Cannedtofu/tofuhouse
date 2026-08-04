"""Utilities for translating uploaded article PDFs into bilingual PDFs."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_MIN_IMAGE_WIDTH = 80
_MIN_IMAGE_HEIGHT = 80
_LINE_GAP_TO_PARAGRAPH = 11


def _markdown_escape_alt(text: str) -> str:
    return (text or "image").replace("[", "(").replace("]", ")")


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    lines: list[dict] = []
    for word in sorted(words, key=lambda w: (round(float(w.get("top", 0)), 1), float(w.get("x0", 0)))):
        top = float(word.get("top", 0))
        bottom = float(word.get("bottom", top))
        text = (word.get("text") or "").strip()
        if not text:
            continue
        if lines and abs(lines[-1]["top"] - top) <= 3:
            lines[-1]["parts"].append(text)
            lines[-1]["bottom"] = max(lines[-1]["bottom"], bottom)
        else:
            lines.append({"top": top, "bottom": bottom, "parts": [text]})
    return [
        {"kind": "text", "top": line["top"], "bottom": line["bottom"], "text": " ".join(line["parts"])}
        for line in lines
    ]


def _extract_text_lines(page) -> list[dict]:
    try:
        raw_lines = page.extract_text_lines(layout=False, strip=True, return_chars=False)
    except Exception:
        raw_lines = None
    if raw_lines:
        lines = []
        for line in raw_lines:
            text = (line.get("text") or "").strip()
            if text:
                lines.append({
                    "kind": "text",
                    "top": float(line.get("top", 0)),
                    "bottom": float(line.get("bottom", line.get("top", 0))),
                    "text": text,
                })
        return lines

    try:
        words = page.extract_words(x_tolerance=2, y_tolerance=3, use_text_flow=True)
    except Exception:
        words = []
    return _group_words_into_lines(words)




def _save_page_images(reader_page, plumber_images: list[dict], image_dir: str, job_id: str, page_num: int) -> list[dict]:
    images = []
    try:
        reader_images = list(reader_page.images)
    except Exception:
        reader_images = []

    sorted_meta = sorted(
        [
            img for img in plumber_images
            if float(img.get("width") or 0) >= _MIN_IMAGE_WIDTH
            and float(img.get("height") or 0) >= _MIN_IMAGE_HEIGHT
        ],
        key=lambda img: (float(img.get("top") or 0), float(img.get("x0") or 0)),
    )

    for idx, meta in enumerate(sorted_meta):
        if idx >= len(reader_images):
            continue
        image = reader_images[idx]
        filename = f"page-{page_num:03d}-image-{idx + 1:02d}.png"
        path = os.path.join(image_dir, filename)
        try:
            pil_image = getattr(image, "image", None)
            if pil_image is not None:
                pil_image.save(path, format="PNG")
            else:
                data = image.data
                with open(path, "wb") as fh:
                    fh.write(data)
        except Exception:
            logger.exception("Failed to save PDF image on page %s", page_num)
            continue

        images.append({
            "kind": "image",
            "top": float(meta.get("top") or 0),
            "bottom": float(meta.get("bottom") or meta.get("top") or 0),
            "markdown": f"![{_markdown_escape_alt(filename)}](/tools/pdf-image/{job_id}/{filename})",
        })
    return images


def _events_to_markdown(events: list[dict]) -> list[str]:
    blocks: list[str] = []
    para_lines: list[str] = []
    last_bottom: float | None = None

    def flush_para():
        nonlocal para_lines
        if para_lines:
            paragraph = " ".join(line.strip() for line in para_lines if line.strip())
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if paragraph:
                blocks.append(paragraph)
            para_lines = []

    for event in sorted(events, key=lambda item: (float(item.get("top") or 0), 0 if item["kind"] == "text" else 1)):
        if event["kind"] == "image":
            flush_para()
            blocks.append(event["markdown"])
            last_bottom = float(event.get("bottom") or event.get("top") or 0)
            continue

        text = (event.get("text") or "").strip()
        if not text:
            continue
        top = float(event.get("top") or 0)
        if last_bottom is not None and top - last_bottom > _LINE_GAP_TO_PARAGRAPH:
            flush_para()
        para_lines.append(text)
        last_bottom = float(event.get("bottom") or top)

    flush_para()
    return blocks


def extract_pdf_markdown(pdf_path: str, image_dir: str, image_job_id: str) -> str:
    """Extract an uploaded PDF as Markdown with inline image placeholders."""
    import pdfplumber
    from pypdf import PdfReader

    os.makedirs(image_dir, exist_ok=True)
    reader = PdfReader(pdf_path)
    pages_md: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            events = _extract_text_lines(page)
            reader_page = reader.pages[page_index] if page_index < len(reader.pages) else None
            if reader_page is not None:
                events.extend(_save_page_images(reader_page, page.images, image_dir, image_job_id, page_index + 1))
            blocks = _events_to_markdown(events)
            if blocks:
                pages_md.extend(blocks)

    return "\n\n".join(pages_md).strip()
