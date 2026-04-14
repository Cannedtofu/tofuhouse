"""LLM summarization via Google Gemini API."""

import logging

import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory

import db
from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_SAFETY = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

_PROMPT_TEMPLATE = """Please summarize the following article in 2-3 concise sentences. \
Focus on the key facts or announcements. Do not include filler phrases like "This article discusses...".

Title: {title}

Content:
{content}

Summary:"""

_BATCH_SIZE = 10


def _get_model():
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        generation_config=genai.types.GenerationConfig(
            temperature=0.2,
            max_output_tokens=256,
        ),
        safety_settings=_SAFETY,
    )


def summarize_new_articles() -> int:
    """
    Summarize all articles in the DB that have no summary yet.
    Returns the number of articles summarized.
    """
    articles = db.get_unsummarized_articles()
    if not articles:
        logger.info("No articles to summarize.")
        return 0

    logger.info("Summarizing %d articles...", len(articles))
    model = _get_model()
    count = 0

    for i in range(0, len(articles), _BATCH_SIZE):
        batch = articles[i : i + _BATCH_SIZE]
        for article in batch:
            title = article["title"] or ""
            content = (article["content"] or "")[:4000]  # cap to avoid token overflow
            if not content.strip():
                db.update_summary(article["id"], "(No content available)")
                continue

            prompt = _PROMPT_TEMPLATE.format(title=title, content=content)
            try:
                response = model.generate_content(prompt)
                summary = response.text.strip()
            except Exception as exc:
                logger.warning("Gemini error for article %d: %s", article["id"], exc)
                summary = "(Summary unavailable)"

            db.update_summary(article["id"], summary)
            count += 1
            logger.debug("  Summarized: %s", title[:60])

    logger.info("Done summarizing. %d articles processed.", count)
    return count


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


def _call_gemini(model, prompt: str, label: str) -> str:
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as exc:
        logger.warning("Gemini error (%s): %s", label, exc)
        return f"(Summary unavailable for {label}: {exc})"


def generate_batch_digest(article_ids: list[int]) -> str:
    """
    Generate a structured digest grouped by source.
    X.com (nitter) sources get separate tweet and retweet summaries.
    RSS/web sources get per-article abstracts.
    Returns a combined string with sections separated by '---'.
    """
    if not article_ids:
        return "(No articles to summarize.)"

    # Group articles by source, preserving insertion order
    sources: dict[int, dict] = {}
    for aid in article_ids:
        a = db.get_article_by_id(aid)
        if not a:
            continue
        sid = a["source_id"]
        if sid not in sources:
            sources[sid] = {
                "name": a["source_name"],
                "type": a["source_type"],
                "articles": [],
            }
        sources[sid]["articles"].append(dict(a))

    if not sources:
        return "(No content available for the selected articles.)"

    model = _get_model()
    sections: list[str] = []

    for src in sources.values():
        source_name = src["name"]
        source_type = src["type"]
        articles = src["articles"]

        if source_type == "nitter":
            tweets = [a for a in articles if not (a.get("title") or "").startswith("RT by @")]
            retweets = [a for a in articles if (a.get("title") or "").startswith("RT by @")]

            parts: list[str] = []

            if tweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:500]}"
                    for a in tweets[:20]
                )
                prompt = _TWEET_PROMPT.format(source_name=source_name, items=items_text)
                parts.append(_call_gemini(model, prompt, f"{source_name} tweets"))

            if retweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:500]}"
                    for a in retweets[:20]
                )
                prompt = _RETWEET_PROMPT.format(source_name=source_name, items=items_text)
                parts.append(_call_gemini(model, prompt, f"{source_name} retweets"))

            if parts:
                sections.append("\n\n".join(parts))

        else:  # rss or web
            items_text = "\n\n".join(
                f"Title: {a['title'] or '(no title)'}\nContent: {(a['content'] or '')[:800]}"
                for a in articles[:20]
            )
            prompt = _ARTICLE_PROMPT.format(source_name=source_name, items=items_text)
            sections.append(_call_gemini(model, prompt, source_name))

    if not sections:
        return "(No digest could be generated.)"

    return "\n\n---\n\n".join(sections)


def summarize_single_article(article_id: int) -> str:
    """
    Generate and store a summary for one article by ID.
    Returns the summary text (or an error string if it fails).
    """
    article = db.get_article_by_id(article_id)
    if not article:
        return "(Article not found)"

    title = article["title"] or ""
    content = (article["content"] or "")[:4000]
    if not content.strip():
        summary = "(No content available)"
        db.update_summary(article_id, summary)
        return summary

    model = _get_model()
    prompt = _PROMPT_TEMPLATE.format(title=title, content=content)
    try:
        response = model.generate_content(prompt)
        summary = response.text.strip()
    except Exception as exc:
        logger.warning("Gemini error for article %d: %s", article_id, exc)
        summary = f"(Summary unavailable: {exc})"

    db.update_summary(article_id, summary)
    return summary
