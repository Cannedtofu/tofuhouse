"""LLM summarization via Qwen (OpenAI-compatible API)."""

from __future__ import annotations

import logging

from openai import OpenAI

import db
from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10


def _get_client() -> OpenAI:
    return OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)


def _chat(client: OpenAI, prompt: str, label: str, max_tokens: int = 512) -> str:
    try:
        resp = client.chat.completions.create(
            model=QWEN_SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Qwen error (%s): %s", label, exc)
        return f"(Summary unavailable for {label}: {exc})"


_PROMPT_TEMPLATE = """Please summarize the following article in 2-3 concise sentences. \
Focus on the key facts or announcements. Do not include filler phrases like "This article discusses...".

Title: {title}

Content:
{content}

Summary:"""

_TWEET_PROMPT = """Summarize the following posts from {source_name} on X.com. Identify the main topics discussed and the overall attitude/opinion/input of the account.
Format:
[Account Name]:
[Discussion on Topic A] [account's opinion/attitude/input on Topic A]
[Discussion on Topic B] [account's opinion/attitude/input on Topic B]
...

Posts:
{items}"""

_RETWEET_PROMPT = """Summarize the following retweets from {source_name} on X.com. Identify what topics {source_name} is amplifying and the overall theme of their retweets.
Format:
[Account Name] Retweets:
[Discussion on Topic A] [overall theme/focus of retweets on Topic A]
[Discussion on Topic B] [overall theme/focus of retweets on Topic B]
...

Retweets:
{items}"""

_ARTICLE_PROMPT = """Provide a brief summary/abstract for each of the following articles from {source_name}.
Format:
{source_name}:
[Article Title]: [Abstract under 200 words]
[Article Title]: [Abstract under 200 words]
...

Articles:
{items}"""


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


def generate_batch_digest(article_ids: list[int]) -> str:
    """
    Generate a structured digest grouped by source.
    Nitter sources get separate tweet / retweet summaries.
    RSS/web sources get per-article abstracts.
    Sections are separated by '---'.
    """
    if not article_ids:
        return "(No articles to summarize.)"

    sources: dict[int, dict] = {}
    for aid in article_ids:
        a = db.get_article_by_id(aid)
        if not a:
            continue
        sid = a["source_id"]
        if sid not in sources:
            sources[sid] = {"name": a["source_name"], "type": a["source_type"], "articles": []}
        sources[sid]["articles"].append(dict(a))

    if not sources:
        return "(No content available for the selected articles.)"

    client = _get_client()
    sections: list[str] = []

    for src in sources.values():
        source_name = src["name"]
        articles = src["articles"]

        if src["type"] == "nitter":
            tweets = [a for a in articles if not (a.get("title") or "").startswith("RT by @")]
            retweets = [a for a in articles if (a.get("title") or "").startswith("RT by @")]
            parts: list[str] = []
            if tweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:500]}" for a in tweets[:20]
                )
                parts.append(_chat(client, _TWEET_PROMPT.format(source_name=source_name, items=items_text), f"{source_name} tweets"))
            if retweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:500]}" for a in retweets[:20]
                )
                parts.append(_chat(client, _RETWEET_PROMPT.format(source_name=source_name, items=items_text), f"{source_name} retweets"))
            if parts:
                sections.append("\n\n".join(parts))
        else:
            items_text = "\n\n".join(
                f"Title: {a['title'] or '(no title)'}\nContent: {(a['content'] or '')[:800]}"
                for a in articles[:20]
            )
            sections.append(_chat(client, _ARTICLE_PROMPT.format(source_name=source_name, items=items_text), source_name, max_tokens=1024))

    return "\n\n---\n\n".join(sections) if sections else "(No digest could be generated.)"


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
