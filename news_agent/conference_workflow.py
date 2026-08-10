"""Comein roadshow crawler and topic matcher."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import conference_db
import db
from article_summarizer import _get_client
from config import QWEN_API_KEY, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

ROADSHOW_URL = "https://www.comein.cn/roadshow/home/all"
_TZ = timezone(timedelta(hours=8))


def _today():
    return datetime.now(_TZ).date()


def _extract_json(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if "```" in raw:
        candidates = re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.S | re.I) + candidates
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except Exception:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except Exception:
                    pass
    return None


def _parse_start(raw_text):
    text = re.sub(r"\s+", " ", raw_text or "")
    year = _today().year
    date_text = ""
    hour = 0
    minute = 0

    match = re.search(r"(20\d{2})[./\u5e74-](\d{1,2})[./\u6708-](\d{1,2})", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        date_text = match.group(0)
    else:
        match = re.search(r"(\d{1,2})[./\u6708-](\d{1,2})(?:\u65e5)?", text)
        if not match:
            return None, ""
        month = int(match.group(1))
        day = int(match.group(2))
        date_text = match.group(0)

    time_match = re.search(r"(\d{1,2})[:\uFF1A](\d{2})", text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=_TZ)
    except ValueError:
        return None, date_text
    if dt.date() < _today() and "20" not in date_text:
        try:
            dt = dt.replace(year=year + 1)
        except ValueError:
            pass
    return dt.isoformat(), date_text


def _clean_title(title, raw_text):
    title = re.sub(r"\s+", " ", title or "").strip()
    if title:
        return title[:240]
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    for line in lines:
        if len(line) >= 6 and not re.search(r"^\d{1,2}[:\uFF1A]\d{2}$", line):
            return line[:240]
    return ""


def _read_items_from_page(page):
    return page.evaluate(
        r"""() => {
          const datePattern = /\d{1,2}[:\uFF1A]\d{2}|\d{1,2}[\u6708.\/-]\d{1,2}|20\d{2}/;
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          const rows = [];
          for (const a of anchors) {
            const href = a.href || '';
            if (!href || href.startsWith('javascript:') || href === location.href) continue;
            let box = a;
            for (let i = 0; i < 4 && box.parentElement; i++) {
              const text = (box.innerText || '').trim();
              if (text.length > 20 && datePattern.test(text)) break;
              box = box.parentElement;
            }
            const rawText = (box.innerText || a.innerText || '').trim();
            if (!rawText || rawText.length < 8) continue;
            if (!datePattern.test(rawText)) continue;
            rows.push({title: (a.innerText || '').trim(), url: href, raw_text: rawText});
          }
          return rows;
        }"""
    )


def crawl_conferences(days=5, max_scrolls=40):
    from playwright.sync_api import sync_playwright

    end_date = _today() + timedelta(days=days)
    seen = {}
    saw_beyond_window = False
    last_count = 0
    still_rounds = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        page.goto(ROADSHOW_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        for _ in range(max_scrolls):
            for raw in _read_items_from_page(page):
                url = urljoin(ROADSHOW_URL, raw.get("url") or "")
                starts_at, date_text = _parse_start(raw.get("raw_text") or "")
                title = _clean_title(raw.get("title"), raw.get("raw_text"))
                if not title or not url:
                    continue
                if starts_at:
                    starts_date = datetime.fromisoformat(starts_at).date()
                    if starts_date > end_date:
                        saw_beyond_window = True
                    if starts_date < _today() or starts_date > end_date:
                        continue
                seen[url] = {
                    "title": title,
                    "url": url,
                    "starts_at": starts_at,
                    "date_text": date_text,
                    "raw_text": raw.get("raw_text") or "",
                }

            if saw_beyond_window:
                break
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1800)
            current_count = len(seen)
            if current_count == last_count:
                still_rounds += 1
            else:
                still_rounds = 0
            last_count = current_count
            if still_rounds >= 4:
                break
        browser.close()

    items = sorted(seen.values(), key=lambda x: (x.get("starts_at") or "9999", x["title"]))
    stats = conference_db.upsert_conferences(items)
    return {
        "fetched": len(items),
        "inserted": stats["inserted"],
        "updated": stats["updated"],
        "stopped_on_beyond_window": saw_beyond_window,
    }


def _keyword_fallback(conferences):
    output = {}
    for conf in conferences:
        text = (conf.get("title") or "").casefold()
        matches = []
        for topic in conf.get("topics_to_match") or []:
            matched = topic.casefold() in text
            matches.append({
                "topic": topic,
                "matched": matched,
                "reason": "keyword direct match" if matched else "",
            })
        output[conf["id"]] = matches
    return output


def _normalize_llm_matches(parsed, conferences):
    allowed_by_id = {
        int(conf["id"]): set(conf.get("topics_to_match") or [])
        for conf in conferences
    }
    matches_by_id = {}
    for row in (parsed or {}).get("matches", []):
        conference_id = int(row.get("conference_id") or 0)
        if conference_id not in allowed_by_id:
            continue
        topic_rows = []
        for item in row.get("topics") or []:
            topic = str(item.get("topic") or "").strip()
            if topic in allowed_by_id[conference_id]:
                topic_rows.append({
                    "topic": topic,
                    "matched": bool(item.get("matched")),
                    "reason": str(item.get("reason") or "")[:300],
                })
        matches_by_id[conference_id] = topic_rows

    for conf in conferences:
        existing = {item["topic"] for item in matches_by_id.get(conf["id"], [])}
        for topic in conf.get("topics_to_match") or []:
            if topic not in existing:
                matches_by_id.setdefault(conf["id"], []).append({
                    "topic": topic,
                    "matched": False,
                    "reason": "LLM returned no decision for this topic.",
                })
    return matches_by_id


def match_conferences_for_user(user_id, force=False):
    topics = conference_db.get_topics(user_id)
    if force:
        conferences = conference_db.list_future_conferences(days=5)
        conference_db.replace_matches(user_id, [row["id"] for row in conferences], {})
        conferences_to_match = [dict(row, topics_to_match=topics) for row in conferences]
    else:
        conferences_to_match = conference_db.list_conferences_missing_matches(user_id, topics, days=5)

    if not topics:
        return {"matched": 0, "topics": 0, "conferences": 0, "llm_called": False}
    if not conferences_to_match:
        return {"matched": 0, "topics": len(topics), "conferences": 0, "llm_called": False}

    if not QWEN_API_KEY:
        matches_by_id = _keyword_fallback(conferences_to_match)
        llm_called = False
    else:
        payload = {
            "conferences": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "time": row.get("starts_at") or row.get("date_text") or "",
                    "topics_to_match": row.get("topics_to_match") or [],
                }
                for row in conferences_to_match
            ],
        }
        prompt = (
            "You are an investment research assistant. Decide whether each conference title is relevant "
            "to each topic listed in topics_to_match. Topics may be companies, people, sectors, products, "
            "technologies, or Chinese/English keywords. Judge only from title and time. Be conservative. "
            "Return compact JSON only with this schema: "
            '{"matches":[{"conference_id":1,"topics":[{"topic":"string","matched":true,"reason":"short reason"}]}]}\n\n'
            f"Data: {json.dumps(payload, ensure_ascii=False)}"
        )
        client = _get_client()
        resp = client.chat.completions.create(
            model=QWEN_SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": "Return JSON only. Do not use Markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        if resp.usage:
            db.log_token_usage(
                user_id=user_id,
                operation="conference_topic_match",
                model=QWEN_SUMMARY_MODEL,
                tokens_in=resp.usage.prompt_tokens,
                tokens_out=resp.usage.completion_tokens,
            )
        parsed = _extract_json(resp.choices[0].message.content)
        matches_by_id = _normalize_llm_matches(parsed, conferences_to_match)
        llm_called = True

    conference_db.upsert_matches(user_id, matches_by_id)
    matched = sum(
        1
        for matches in matches_by_id.values()
        for match in matches
        if match.get("matched")
    )
    return {
        "matched": matched,
        "topics": len(topics),
        "conferences": len(conferences_to_match),
        "llm_called": llm_called,
    }


def refresh_for_user(user_id):
    crawl_result = crawl_conferences(days=5)
    match_result = match_conferences_for_user(user_id)
    return {"crawl": crawl_result, "match": match_result}