"""LLM summarization via Qwen (OpenAI-compatible API)."""

from __future__ import annotations

import hashlib
import json
import logging
import re

from openai import OpenAI

import db
from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10

_SYSTEM_MESSAGE = (
    "You are briefing a reader focused on AI, technology, venture capital, and investment. "
    "Always respond in Simplified Chinese."
)


def _get_client() -> OpenAI:
    return OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)


def _chat(
    client: OpenAI,
    prompt: str,
    label: str,
    max_tokens: int = 512,
    system: str | None = _SYSTEM_MESSAGE,
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
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Qwen error (%s): %s", label, exc)
        return f"(Summary unavailable for {label}: {exc})"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
用2-3句话概括以下文章的核心内容。聚焦关键事实或公告，不要使用"本文讨论了……"之类的套话。

Title: {title}

Content:
{content}

Summary:"""


# Article briefing: classify articles by importance and explain why each matters.
# Articles are numbered so we can parse which ones get full abstracts.
# NOTE: The Significant/Notable/Skip labels MUST stay in English — the parser depends on them.
_ARTICLE_BRIEFING_PROMPT = """\
以下是来自 {source_name} 的 {n} 篇文章，请按其对AI/科技/VC读者的重要性进行分类。

{numbered_articles}

严格按照以下格式输出（分类标签必须保持英文原样，其余内容用简体中文）：
Significant: [逗号分隔的编号，或填 "none"]
Notable: [逗号分隔的编号，或填 "none"]
Skip: [逗号分隔的编号，或填 "none"]

然后对每篇 Significant 和 Notable 的文章各写一行（用简体中文）：
[编号]. [标题] — [一句话说明其重要性]"""


# Abstract for a single significant/notable article (~80-100 words).
_ARTICLE_ABSTRACT_PROMPT = """\
用80-100个中文字概括以下文章。以核心发现或公告开篇，包含最重要的支撑细节。不要使用"本文讨论了"或"在这篇文章中"之类的套话。全部用简体中文输出。

Title: {title}
Content: {content}"""


# Twitter: original posts — capture positions and discourse, not just topics.
_TWEET_PROMPT = """\
基于以下推文，概括 {source_name} 正在思考什么。全部用简体中文输出。

对每个重要话题：
- {source_name} 提出了什么具体立场或主张？（直接表述，有用时可引用原文）
- 这是新观点、对某事的回应，还是持续讨论的延续？

最后用一句话说明 {source_name} 的思维方向。
面向熟悉该领域的读者，写分析性简报，而非内容复述。

Posts:
{items}"""


# Twitter: retweets — what is the account amplifying and why.
_RETWEET_PROMPT = """\
基于以下转推内容，概括 {source_name} 正在放大哪些信息。全部用简体中文输出。

对每个重要话题：
- 在说什么，谁说的（如果是值得关注的人）？
- {source_name} 选择转发这些内容，反映了其怎样的优先关注点？

最后用一句话总结 {source_name} 此期间转发内容的整体主题。

Retweets:
{items}"""


# Big picture: cross-source synthesis from per-source briefing outputs.
_BIG_PICTURE_PROMPT = """\
以下是本期摘要中 {n} 个信源的简报内容：

{all_briefings}

请识别跨信源的3-5个主导性主题，对每个主题：
- 用一句话陈述该主题
- 引用支持该主题的具体信源或文章
- 指出各信源在该主题上的收敛点或分歧点

最后标注仅出现在单一信源中的重要信号（即便没有其他佐证，也值得关注）。

要求具体：点名公司、模型、论文和相关人物。不得在原文依据之外进行推测。全部用简体中文输出。"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_briefing_tiers(text: str) -> tuple[set[int], set[int]]:
    """Parse article numbers from Significant and Notable lines in a briefing output."""
    significant: set[int] = set()
    notable: set[int] = set()
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("significant:"):
            significant = {int(n) for n in re.findall(r"\d+", line)}
        elif stripped.startswith("notable:"):
            notable = {int(n) for n in re.findall(r"\d+", line)}
    return significant, notable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    For each RSS/web source:
      - Call 1: briefing — classifies articles as Significant / Notable / Skip with
                one-line "why it matters" annotations.
      - Call 2: abstracts — 80-100 word abstract for each Significant/Notable article.

    For Nitter sources: tweet + retweet discourse summaries (unchanged structure,
    improved prompts).

    A Big Picture synthesis section is prepended when more than one source is present,
    built from the per-source briefing outputs so it stays cite-backed and grounded.
    """
    if not article_ids:
        return "(No articles to summarize.)"

    # Check full digest cache — same sorted article IDs = same digest
    sorted_ids = sorted(article_ids)
    ids_json = json.dumps(sorted_ids)
    ids_hash = hashlib.sha256(ids_json.encode()).hexdigest()
    cached = db.get_digest_cache(ids_hash)
    if cached:
        logger.info("Digest cache hit (%s articles)", len(article_ids))
        return cached

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
    briefing_outputs: list[str] = []  # collected for big picture synthesis

    # RSS/web sources first, nitter last
    sorted_sources = sorted(sources.values(), key=lambda s: 1 if s["type"] == "nitter" else 0)

    for src in sorted_sources:
        source_name = src["name"]
        articles = src["articles"]

        if src["type"] == "nitter":
            tweets = [a for a in articles if not (a.get("title") or "").startswith("RT by @")]
            retweets = [a for a in articles if (a.get("title") or "").startswith("RT by @")]
            parts: list[str] = []

            if tweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:800]}" for a in tweets[:20]
                )
                result = _chat(
                    client,
                    _TWEET_PROMPT.format(source_name=source_name, items=items_text),
                    f"{source_name} tweets",
                    max_tokens=600,
                )
                parts.append(result)
                briefing_outputs.append(f"[{source_name} — original posts]\n{result}")

            if retweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:800]}" for a in retweets[:20]
                )
                result = _chat(
                    client,
                    _RETWEET_PROMPT.format(source_name=source_name, items=items_text),
                    f"{source_name} retweets",
                    max_tokens=600,
                )
                parts.append(result)
                briefing_outputs.append(f"[{source_name} — retweets]\n{result}")

            if parts:
                sections.append(f"### {source_name}\n\n" + "\n\n".join(parts))

        else:
            capped = articles[:20]
            n = len(capped)

            # Call 1: briefing + classification — use first half of each article's content
            numbered_articles = "\n\n".join(
                f"{i + 1}. Title: {a['title'] or '(no title)'}\n"
                f"   Content: {(a['content'] or '')[:max(len(a['content'] or '') // 2, 500)]}"
                for i, a in enumerate(capped)
            )
            briefing = _chat(
                client,
                _ARTICLE_BRIEFING_PROMPT.format(
                    n=n, source_name=source_name, numbered_articles=numbered_articles
                ),
                f"{source_name} briefing",
                max_tokens=700,
            )
            briefing_outputs.append(f"[{source_name}]\n{briefing}")

            # Call 2: abstracts for significant + notable articles
            sig_nums, notable_nums = _parse_briefing_tiers(briefing)
            worth_reading = sig_nums | notable_nums

            abstracts: list[str] = []
            for i, a in enumerate(capped):
                if (i + 1) in worth_reading:
                    # Use cached abstract if available; otherwise generate and store
                    abstract = db.get_digest_abstract(a["id"])
                    if abstract:
                        logger.info("Abstract cache hit: article %s", a["id"])
                    else:
                        abstract = _chat(
                            client,
                            _ARTICLE_ABSTRACT_PROMPT.format(
                                title=a["title"] or "(no title)",
                                content=a["content"] or "",
                            ),
                            f"{source_name} abstract {i + 1}",
                            max_tokens=200,
                        )
                        db.update_digest_abstract(a["id"], abstract)
                    title_text = a["title"] or "(no title)"
                    abstracts.append(f"**[{title_text}]({a['url']})**\n{abstract}")

            section = f"### {source_name}\n\n{briefing}"
            if abstracts:
                section += "\n\n**Abstracts**\n\n" + "\n\n".join(abstracts)
            sections.append(section)

    # Big picture synthesis — prepended, only when multiple sources present
    if len(sources) > 1 and briefing_outputs:
        all_briefings = "\n\n".join(briefing_outputs)
        big_picture = _chat(
            client,
            _BIG_PICTURE_PROMPT.format(n=len(sources), all_briefings=all_briefings),
            "big picture",
            max_tokens=1000,
        )
        sections.insert(0, f"## Big Picture\n\n{big_picture}")

    result = "\n\n---\n\n".join(sections) if sections else "(No digest could be generated.)"
    db.save_digest_cache(ids_hash, ids_json, result)
    return result


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
