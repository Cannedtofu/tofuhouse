"""
Two-tier article content extractor.

Tier 1 — Playwright only (fast, no LLM):
  Opens a headed browser, waits for the page, extracts with trafilatura.
  Handles JS-rendered pages and basic bot detection.

Tier 2 — browser-use agent + Qwen (last resort):
  Used only when Playwright returns fewer than MIN_EXTRACTED_CHARS characters.
  The LLM agent can interact with the page (scroll, dismiss banners, etc.)
  before extracting content.

Requirements:
  - Python 3.11+
  - pip install playwright trafilatura browser-use langchain-openai
  - playwright install chromium
  - QWEN_API_KEY in .env (only needed if Tier 2 is triggered)

Usage:
  python -m fetchers.browser_use_fetcher <url>
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import trafilatura
from bs4 import BeautifulSoup, NavigableString, Tag
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Minimum extracted characters before falling back to the agent
MIN_EXTRACTED_CHARS = 300


# ---------------------------------------------------------------------------
# Image-aware HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _get_img_url(tag: Tag) -> str:
    """
    Return the best permanent image URL from an <img> tag.

    Priority:
    1. data-attrs JSON (Substack embeds the clean S3 URL here — no expiring CDN tokens)
    2. Standard src / lazy-load attributes
    3. srcset first entry
    """
    import json as _json

    # Substack (and some other platforms) embed full metadata as JSON in data-attrs
    raw_attrs = tag.get("data-attrs", "")
    if raw_attrs:
        try:
            attrs = _json.loads(raw_attrs)
            src = attrs.get("src", "")
            if src and src.startswith("http"):
                return src
        except Exception:
            pass

    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        val = tag.get(attr, "")
        if val and not val.startswith("data:") and val.startswith("http"):
            # Strip Substack CDN signing tokens ($s_...!) — they expire
            # e.g. https://substackcdn.com/image/fetch/$s_!abc!,w_1456,.../<encoded-url>
            # Clean version:  https://substackcdn.com/image/fetch/f_auto,.../<encoded-url>
            import re as _re
            val = _re.sub(r"\$s_[^,!]+!,?", "", val)
            return val

    srcset = tag.get("srcset", "")
    if srcset:
        first = srcset.strip().split(",")[0].strip().split()[0]
        if first.startswith("http"):
            return first
    return ""


def _soup_to_markdown(el: Tag) -> str:
    """Recursively convert a BeautifulSoup element to Markdown, images included."""
    parts = []
    for child in el.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text.strip():
                parts.append(text)
        elif isinstance(child, Tag):
            name = child.name
            if name in ("p", "div", "section", "article"):
                inner = _soup_to_markdown(child).strip()
                if inner:
                    parts.append(inner + "\n\n")
            elif name in ("h1", "h2", "h3", "h4"):
                inner = child.get_text(strip=True)
                if inner:
                    parts.append("#" * int(name[1]) + " " + inner + "\n\n")
            elif name == "figure":
                # Substack: <figure><a><div><picture><img></picture></div></a><figcaption>
                img = child.find("img")
                caption = child.find("figcaption")
                cap_text = caption.get_text(strip=True) if caption else ""
                if img:
                    url = _get_img_url(img)
                    alt = cap_text or img.get("alt", "") or ""
                    if url:
                        parts.append(f"\n![{alt}]({url})\n")
                        if cap_text:
                            parts.append(f"*{cap_text}*\n")
                parts.append("\n")
            elif name == "picture":
                # <picture> without a wrapping <figure> — grab the inner <img>
                img = child.find("img")
                if img:
                    url = _get_img_url(img)
                    if url:
                        alt = img.get("alt", "") or ""
                        parts.append(f"\n![{alt}]({url})\n\n")
            elif name == "img":
                url = _get_img_url(child)
                if url:
                    alt = child.get("alt", "") or ""
                    parts.append(f"\n![{alt}]({url})\n\n")
            elif name in ("strong", "b"):
                inner = child.get_text(strip=True)
                if inner:
                    parts.append(f"**{inner}**")
            elif name in ("em", "i"):
                inner = child.get_text(strip=True)
                if inner:
                    parts.append(f"*{inner}*")
            elif name == "a":
                inner = child.get_text(strip=True)
                href = child.get("href", "")
                if inner and href:
                    parts.append(f"[{inner}]({href})")
                elif inner:
                    parts.append(inner)
            elif name == "br":
                parts.append("\n")
            elif name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    inner = li.get_text(strip=True)
                    parts.append(f"- {inner}\n")
                parts.append("\n")
            elif name == "blockquote":
                inner = child.get_text(strip=True)
                parts.append(f"> {inner}\n\n")
            elif name == "pre":
                parts.append(f"```\n{child.get_text()}\n```\n\n")
            elif name not in ("script", "style", "nav", "header", "footer"):
                parts.append(_soup_to_markdown(child))
    return "".join(parts)


def _extract_with_images(html: str) -> str:
    """
    Extract article text as Markdown with images in their correct positions.

    Strategy:
    1. Ask trafilatura for clean article HTML (output_format='html') — this
       gives us the article body with noise stripped.
    2. Convert that HTML to Markdown via _soup_to_markdown, which handles
       <img>, <figure>, lazy-load data-src attributes, etc.
    3. If trafilatura's HTML output contains no images (common on Substack),
       fall back to scanning the original full-page HTML for <img> tags and
       distributing them evenly between paragraphs.
    """
    # Step 1: trafilatura clean HTML
    article_html = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        include_images=True,
        output_format="html",
        favor_precision=True,
    ) or ""

    if article_html:
        soup = BeautifulSoup(article_html, "html.parser")
        md = _soup_to_markdown(soup).strip()
        if "![" in md:
            logger.info("[extract] trafilatura HTML contained images — using direct conversion")
            return md

    # Step 2: trafilatura text + manual image injection from original HTML
    text_md = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        output_format="markdown",
    ) or ""

    # Collect content images from original HTML (filter out icons/trackers)
    raw_soup = BeautifulSoup(html, "html.parser")
    images: list[str] = []
    for img in raw_soup.find_all("img"):
        url = _get_img_url(img)
        if not url:
            continue
        # Skip tiny tracking pixels and icons (width/height ≤ 4px when stated)
        try:
            w = int(img.get("width", 100))
            h = int(img.get("height", 100))
            if w <= 4 or h <= 4:
                continue
        except (ValueError, TypeError):
            pass
        alt = img.get("alt", "")
        images.append(f"![{alt}]({url})")

    if not images:
        logger.info("[extract] no images found in page HTML")
        return text_md

    logger.info("[extract] injecting %d image(s) into article text", len(images))

    # Distribute images evenly between paragraphs
    paragraphs = [p for p in text_md.split("\n\n") if p.strip()]
    if not paragraphs:
        return text_md + "\n\n" + "\n\n".join(images)

    step = max(1, len(paragraphs) // (len(images) + 1))
    result: list[str] = []
    img_idx = 0
    for i, para in enumerate(paragraphs):
        result.append(para)
        if img_idx < len(images) and (i + 1) % step == 0:
            result.append(images[img_idx])
            img_idx += 1
    result.extend(images[img_idx:])
    return "\n\n".join(result)


# ---------------------------------------------------------------------------
# Tier 1: Playwright only
# ---------------------------------------------------------------------------

async def _playwright_fetch(url: str) -> str:
    """Navigate with a headed Playwright browser, extract text with trafilatura."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--lang=en-US"],
        )
        context = await browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        try:
            logger.info("[playwright] Loading: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(2_000)
            # Scroll to bottom and back to trigger lazy-loaded images
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1_500)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
            html = await page.content()
            logger.info("[playwright] page HTML: %d chars", len(html))
        finally:
            await browser.close()

    text = _extract_with_images(html)
    logger.info("[playwright] extracted: %d chars", len(text))
    return text


# ---------------------------------------------------------------------------
# Tier 2: browser-use agent (last resort)
# ---------------------------------------------------------------------------

def _make_qwen_llm():
    from browser_use.llm.openai.like import ChatOpenAILike

    api_key = os.getenv("QWEN_API_KEY")
    if not api_key:
        raise RuntimeError("QWEN_API_KEY is not set in .env — needed for browser-use fallback")

    return ChatOpenAILike(
        model="qwen-vl-max",
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        add_schema_to_system_prompt=True,
    )


async def _agent_fetch(url: str) -> str:
    """Use a browser-use LLM agent to extract content when Playwright alone isn't enough."""
    from browser_use import Agent
    from browser_use.browser.profile import BrowserProfile

    profile = BrowserProfile(
        args=["--lang=en-US", "--accept-lang=en-US"],
        headless=False,
    )
    task = (
        f"Navigate to {url}. "
        "Once the page is fully loaded, extract ONLY the main article body text — "
        "no menus, headers, footers, sidebars, or ads. "
        "Call done() with the full article text as your final answer."
    )
    agent = Agent(task=task, llm=_make_qwen_llm(), use_vision=True, browser_profile=profile)
    result = await agent.run()

    text = result.final_result()
    if not text:
        parts = result.extracted_content()
        text = "\n\n".join(parts) if parts else ""

    logger.info("[browser-use agent] extracted: %d chars", len(text))
    return text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def _fetch_async(url: str) -> str:
    """Tier 1 first; fall back to Tier 2 only if content is too short."""
    text = await _playwright_fetch(url)

    if len(text) >= MIN_EXTRACTED_CHARS:
        return text

    logger.warning(
        "[playwright] only %d chars — below threshold (%d), trying browser-use agent…",
        len(text), MIN_EXTRACTED_CHARS,
    )
    try:
        agent_text = await _agent_fetch(url)
        if len(agent_text) > len(text):
            return agent_text
    except Exception as exc:
        logger.warning("[browser-use agent] failed: %s", exc)

    return text


def fetch_article(url: str) -> str:
    """Synchronous wrapper — use this from the main pipeline."""
    return asyncio.run(_fetch_async(url))


# ---------------------------------------------------------------------------
# Batch enrichment (drop-in for _enrich_with_selenium in rss.py)
# ---------------------------------------------------------------------------

def enrich_with_playwright(articles: list[dict]) -> list[dict]:
    """
    Enrich articles flagged needs_full_content=True.
    Same interface as _enrich_with_selenium() — modifies articles in-place.
    """
    to_fetch = [a for a in articles if a.get("needs_full_content")]
    if not to_fetch:
        for a in articles:
            a.pop("needs_full_content", None)
        return articles

    logger.info("[playwright] Fetching full content for %d article(s)…", len(to_fetch))

    for article in to_fetch:
        try:
            text = fetch_article(article["url"])
            if text and len(text) > len(article.get("content", "")):
                article["content"] = text
        except Exception as exc:
            logger.warning("[playwright] skipping %s — %s", article["url"], exc)
        article.pop("needs_full_content", None)

    for a in articles:
        a.pop("needs_full_content", None)

    return articles


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m fetchers.browser_use_fetcher <url>")
        sys.exit(1)

    target_url = sys.argv[1]
    print(f"\nFetching: {target_url}\n{'='*60}")

    content = fetch_article(target_url)

    if content:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir=project_root, encoding="utf-8"
        ) as f:
            f.write(content)
            out_path = f.name
        print(f"\nSaved {len(content)} chars → {out_path}")
    else:
        print("No content extracted.")
