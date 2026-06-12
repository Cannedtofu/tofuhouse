"""Paragraph-by-paragraph bilingual translation for news articles.

Takes a markdown article and returns bilingual markdown where each
English paragraph is immediately followed by a Chinese blockquote.
"""
from __future__ import annotations

import logging
import re

from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

_BATCH_SIZE = 6  # paragraphs per API call

_TRANSLATE_SYSTEM = (
    "You are a professional translator. "
    "The user will send numbered paragraph tags: <p num=\"N\">text</p>. "
    "Translate each paragraph from English to Simplified Chinese. "
    "Return the same XML tags with translated content inside, e.g. <p num=\"N\">中文</p>. "
    "Preserve inline markdown: **bold**, *italic*, [link text](url). "
    "Output ONLY the translated XML tags, nothing else."
)


def _is_translatable(p: str) -> bool:
    """True when a paragraph contains enough prose to be worth translating."""
    s = p.strip()
    if len(s) < 20:
        return False
    # Pure image line
    if re.fullmatch(r'!\[[^\]]*\]\([^)]+\)', s):
        return False
    # Code fence
    if s.startswith("```"):
        return False
    # Horizontal rule
    if re.fullmatch(r"[-*_]{3,}", s):
        return False
    # Bare URL
    if re.fullmatch(r"https?://\S+", s):
        return False
    # Contains some letter characters (not purely symbols/numbers/punctuation)
    if not re.search(r"[a-zA-Z一-鿿]{5,}", s):
        return False
    return True


def _translate_batch(pairs: list[tuple[int, str]]) -> dict[int, str]:
    """Send a batch of (index, text) to qwen-plus; return {index: zh_text}."""
    from openai import OpenAI

    tagged = "\n\n".join(f'<p num="{i}">{text}</p>' for i, text in pairs)
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    resp = client.chat.completions.create(
        model=QWEN_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user",   "content": tagged},
        ],
        temperature=0.1,
    )
    output = resp.choices[0].message.content.strip()

    result: dict[int, str] = {}
    for m in re.finditer(r'<p num="(\d+)">(.*?)</p>', output, re.DOTALL):
        result[int(m.group(1))] = m.group(2).strip()
    return result


def translate_article_bilingual(content: str) -> str:
    """
    Translate an article's markdown content into bilingual EN+ZH format.

    Each translatable English paragraph is followed by a blockquote
    containing the Chinese translation:

        English paragraph text here.

        > 中文翻译在这里。

    Non-translatable paragraphs (images, code blocks, very short lines)
    are left unchanged with no Chinese counterpart.
    """
    # Split on one or more blank lines
    raw_paras = re.split(r"\n{2,}", content)
    paragraphs = [p.strip() for p in raw_paras]

    # Identify paragraphs that need translation (track original index)
    to_translate: list[tuple[int, str]] = [
        (i, p) for i, p in enumerate(paragraphs) if _is_translatable(p)
    ]

    logger.info(
        "Translating article: %d/%d paragraphs selected",
        len(to_translate), len(paragraphs),
    )

    # Strip inline images before sending to the API — the translated Chinese blockquote
    # should be text-only. The original English paragraph (emitted as-is above it)
    # already carries the images, so nothing is lost.
    _IMG_RE = re.compile(r'!\[[^\]]*\]\([^)]+\)')
    to_translate_clean = [
        (i, _IMG_RE.sub("", p).strip()) for i, p in to_translate
    ]
    # Drop paragraphs that became empty after stripping (image-only but _is_translatable
    # returned True due to surrounding text — strip left nothing)
    to_translate_clean = [(i, p) for i, p in to_translate_clean if p]

    # Batch translate
    all_zh: dict[int, str] = {}
    for start in range(0, len(to_translate_clean), _BATCH_SIZE):
        batch = to_translate_clean[start : start + _BATCH_SIZE]
        try:
            zh_map = _translate_batch(batch)
            all_zh.update(zh_map)
            logger.info("Translated batch paragraphs %d–%d", start, start + len(batch) - 1)
        except Exception as exc:
            logger.warning("Batch translation failed (paragraphs %d–%d): %s", start, start + len(batch) - 1, exc)

    # Build bilingual output
    out: list[str] = []
    for i, para in enumerate(paragraphs):
        if not para:
            continue
        out.append(para)
        if i in all_zh:
            zh = all_zh[i]
            # Format as blockquote (one > per line to handle multi-line zh)
            bq = "\n".join(
                f"> {line}" if line.strip() else ">"
                for line in zh.split("\n")
            )
            out.append(bq)

    return "\n\n".join(out)
