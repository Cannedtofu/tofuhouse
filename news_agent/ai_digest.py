"""AI-powered digest generation for the web UI.

Produces the structured digest shown when the user clicks "Generate AI Digest":
  - Per RSS/web source: importance classification + 80-100 word abstracts
  - Per Nitter source: original-tweet discourse + retweet signal summaries
  - Cross-source Big Picture synthesis when multiple sources are present

For plain-text email digest formatting see email_digest.py.
For per-article summarization see article_summarizer.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

import db
from article_summarizer import _chat, _get_client, _SYSTEM_MESSAGE
from config import QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Article briefing: classify articles by importance and explain why each matters.
# NOTE: Significant/Notable/Skip labels MUST stay in English — the parser depends on them.
_ARTICLE_BRIEFING_PROMPT = """\
以下是来自 {source_name} 的 {n} 篇文章，请按其对AI/科技/VC读者的重要性进行分类，并按以下固定结构输出（分类标签必须保持英文原样，其余内容用简体中文）：

{numbered_articles}

**Significant**
[编号]. [标题] — [一句话说明其重要性]
（如无，填 none）

**Notable**
[编号]. [标题] — [一句话说明其重要性]
（如无，填 none）

**Skip**
[逗号分隔的编号，如无填 none]"""


_ARTICLE_ABSTRACT_PROMPT = """\
用80-100个中文字概括以下文章。以核心发现或公告开篇，包含最重要的支撑细节。不要使用"本文讨论了"或"在这篇文章中"之类的套话。全部用简体中文输出。

Title: {title}
Content: {content}"""


_TWEET_PROMPT = """\
基于以下推文，概括 {source_name} 正在思考什么。全部用简体中文输出。

对每个重要话题：
- {source_name} 提出了什么具体立场或主张？（直接表述，有用时可引用原文）
- 这是新观点、对某事的回应，还是持续讨论的延续？

最后用一句话说明 {source_name} 的思维方向。
面向熟悉该领域的读者，写分析性简报，而非内容复述。

Posts:
{items}"""


_RETWEET_PROMPT = """\
基于以下转推内容，写一段分析性简报，说明 {source_name} 此期间在放大什么信息、体现其怎样的优先判断。全部用简体中文输出。

采用"结论先行"结构：先用1-2句点明整体信号，再以具体转推内容作为佐证（可点名作者或核心主张）。面向熟悉该领域的读者，写分析而非逐条列举。

Retweets:
{items}"""


_BIG_PICTURE_PROMPT = """\
以下是本期摘要中 {n} 个信源的简报内容：

{all_briefings}

直接输出以下结构，不加前言、自评或格式说明。识别3-5个跨信源主导性主题，每个主题严格按此格式：

**主题：**[一句话标题]
**依据：**[具体信源名称 + 原文核心主张，点名公司/模型/人物]
**收敛与分歧：**[各信源立场的共识或张力]

所有主题输出完毕后，另起一段，标题"**孤立信号**"，列出仅出现在单一信源中但值得关注的内容。

不在原文依据之外推测。全部简体中文输出。"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_briefing_tiers(text: str) -> tuple[set[int], set[int]]:
    """Parse article numbers from grouped Significant / Notable sections."""
    significant: set[int] = set()
    notable: set[int] = set()
    current: str | None = None
    for line in text.splitlines():
        clean = line.strip().lower().lstrip("#*").rstrip("*:").strip()
        if clean == "significant":
            current = "significant"
            continue
        elif clean == "notable":
            current = "notable"
            continue
        elif clean == "skip":
            current = None
            continue
        if current in ("significant", "notable"):
            nums = {int(n) for n in re.findall(r"\d+", line)}
            if current == "significant":
                significant |= nums
            else:
                notable |= nums
    return significant, notable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_batch_digest(article_ids: list[int], user_id: int | None = None) -> str:
    """
    Generate a structured AI digest grouped by source.

    For each RSS/web source:
      - Call 1: briefing — classifies articles as Significant / Notable / Skip
      - Call 2: 80-100 word abstracts for Significant/Notable articles

    For Nitter sources: tweet discourse + retweet signal summaries.

    Big Picture synthesis prepended when more than one source is present.
    Result is cached by sorted article ID hash.
    """
    if not article_ids:
        return "(No articles to summarize.)"

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
    briefing_outputs: list[str] = []
    _acc: list[tuple[int, int]] = []

    # RSS/web sources first, nitter last
    sorted_sources = sorted(sources.values(), key=lambda s: 1 if s["type"] == "nitter" else 0)

    for src in sorted_sources:
        source_name = src["name"]
        articles = src["articles"]

        if src["type"] == "nitter":
            tweets   = [a for a in articles if not (a.get("title") or "").startswith("RT by @")]
            retweets = [a for a in articles if     (a.get("title") or "").startswith("RT by @")]
            parts: list[str] = []

            if tweets:
                items_text = "\n\n".join(
                    f"- {a['title'] or ''}\n{(a['content'] or '')[:800]}" for a in tweets[:20]
                )
                result = _chat(
                    client,
                    _TWEET_PROMPT.format(source_name=source_name, items=items_text),
                    f"{source_name} tweets",
                    max_tokens=800,
                    _acc=_acc,
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
                    max_tokens=700,
                    _acc=_acc,
                )
                parts.append(result)
                briefing_outputs.append(f"[{source_name} — retweets]\n{result}")

            if parts:
                sections.append(f"### {source_name}\n\n" + "\n\n".join(parts))

        else:
            capped = articles[:20]
            n = len(capped)

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
                max_tokens=900,
                _acc=_acc,
            )
            briefing_outputs.append(f"[{source_name}]\n{briefing}")

            sig_nums, notable_nums = _parse_briefing_tiers(briefing)
            worth_reading = sig_nums | notable_nums

            abstracts: list[str] = []
            for i, a in enumerate(capped):
                if (i + 1) in worth_reading:
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
                            _acc=_acc,
                        )
                        db.update_digest_abstract(a["id"], abstract)
                    title_text = a["title"] or "(no title)"
                    abstracts.append(f"**[{title_text}]({a['url']})**\n{abstract}")

            section = f"### {source_name}\n\n{briefing}"
            if abstracts:
                section += "\n\n**Abstracts**\n\n" + "\n\n".join(abstracts)
            sections.append(section)

    if len(sources) > 1 and briefing_outputs:
        all_briefings = "\n\n".join(briefing_outputs)
        big_picture = _chat(
            client,
            _BIG_PICTURE_PROMPT.format(n=len(sources), all_briefings=all_briefings),
            "big picture",
            max_tokens=1200,
            _acc=_acc,
        )
        sections.insert(0, f"## Big Picture\n\n{big_picture}")

    result = "\n\n---\n\n".join(sections) if sections else "(No digest could be generated.)"
    db.save_digest_cache(ids_hash, ids_json, result)

    if _acc:
        total_in  = sum(t[0] for t in _acc)
        total_out = sum(t[1] for t in _acc)
        db.log_token_usage("digest", QWEN_SUMMARY_MODEL, total_in, total_out, user_id=user_id)
        logger.info("Digest token usage: %d in / %d out (%d calls)", total_in, total_out, len(_acc))

    return result
