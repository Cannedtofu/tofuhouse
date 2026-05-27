"""Per-article summarization via Qwen (OpenAI-compatible API).

Generates the 2-3 sentence blurb shown under each article in the feed.
For AI digest generation see ai_digest.py.
"""

from __future__ import annotations

import logging

from openai import OpenAI

import db
from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10

_SYSTEM_MESSAGE = (
    "You are briefing a reader focused on AI, technology, venture capital, and investment. "
    "Always respond in Simplified Chinese."
)

_PROMPT_TEMPLATE = """\
用2-3句话概括以下文章的核心内容。聚焦关键事实或公告，不要使用"本文讨论了……"之类的套话。

Title: {title}

Content:
{content}

Summary:"""


def _get_client() -> OpenAI:
    return OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)


def _chat(
    client: OpenAI,
    prompt: str,
    label: str,
    max_tokens: int = 512,
    system: str | None = _SYSTEM_MESSAGE,
    _acc: list | None = None,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = client.chat.completions.create(
            model=QWEN_SUMMARY_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        if _acc is not None and resp.usage:
            _acc.append((resp.usage.prompt_tokens, resp.usage.completion_tokens))
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Qwen error (%s): %s", label, exc)
        return f"(Summary unavailable for {label}: {exc})"


def summarize_new_articles() -> int:
    """Summarize all DB articles that have no summary yet. Returns count processed."""
    articles = db.get_unsummarized_articles()
    if not articles:
        logger.info("No articles to summarize.")
        return 0

    logger.info("Summarizing %d articles...", len(articles))
    client = _get_client()
    count = 0

    for i in range(0, len(articles), _BATCH_SIZE):
        for article in articles[i : i + _BATCH_SIZE]:
            title = article["title"] or ""
            content = (article["content"] or "")[:4000]
            if not content.strip():
                db.update_summary(article["id"], "(No content available)")
                continue
            prompt = _PROMPT_TEMPLATE.format(title=title, content=content)
            summary = _chat(client, prompt, label=f"article {article['id']}")
            db.update_summary(article["id"], summary)
            count += 1

    logger.info("Done summarizing. %d articles processed.", count)
    return count


def summarize_single_article(article_id: int) -> str:
    """Generate and store a summary for one article. Returns the summary text."""
    article = db.get_article_by_id(article_id)
    if not article:
        return "(Article not found)"
    title = article["title"] or ""
    content = (article["content"] or "")[:4000]
    if not content.strip():
        summary = "(No content available)"
        db.update_summary(article_id, summary)
        return summary
    client = _get_client()
    prompt = _PROMPT_TEMPLATE.format(title=title, content=content)
    summary = _chat(client, prompt, label=f"article {article_id}")
    db.update_summary(article_id, summary)
    return summary
