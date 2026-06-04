"""Fetcher for Bilibili user space videos via WBI-signed REST API.

Bilibili's API requires WBI request signing (md5 of sorted params + mixin key derived
from rotating img/sub keys). Keys are fetched once from /x/web-interface/nav and
cached in-process for 6 hours. buvid cookies are obtained from /x/frontend/finger/spi.

Public API:
  fetch_bilibili(source_url, known_urls, date_from) -> list[dict]
  extract_uid(url)                                  -> str | None
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from config import DATE_RANGE_DAYS, MAX_ARTICLES_PER_SOURCE

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# WBI mixin key derivation table (official, stable across API versions)
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18,  2, 53,  8, 23, 32, 15, 50, 10, 31, 58,  3, 45, 35,
    27, 43,  5, 49, 33,  9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48,  7, 16, 24, 55, 40, 61, 26, 17,  0,  1, 60, 51, 30,  4,
    22, 25, 54, 21, 56, 59,  6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# In-process cache — refreshed every 6 hours
_cache: dict = {
    "img_key": "",
    "sub_key": "",
    "buvid3":  "",
    "buvid4":  "",
    "expires": 0.0,
}

_UID_RE = re.compile(r"space\.bilibili\.com/(\d+)", re.I)


def extract_uid(url: str) -> str | None:
    """Extract numeric UID from https://space.bilibili.com/{uid}/…"""
    m = _UID_RE.search(url)
    return m.group(1) if m else None


def _refresh_cache() -> None:
    """Fetch fresh WBI keys and buvid cookies, store in module-level cache."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    # buvid cookies (no login required — spi endpoint is always public)
    try:
        spi = session.get(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=10
        ).json()
        _cache["buvid3"] = spi["data"]["b_3"]
        _cache["buvid4"] = spi["data"]["b_4"]
    except Exception as exc:
        log.warning("Bilibili: failed to get buvid cookies: %s", exc)

    # WBI image/sub keys
    try:
        nav = session.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={"Referer": "https://www.bilibili.com"},
            timeout=10,
        ).json()
        img_url = nav["data"]["wbi_img"]["img_url"]
        sub_url = nav["data"]["wbi_img"]["sub_url"]
        _cache["img_key"] = img_url.rsplit("/", 1)[-1].split(".")[0]
        _cache["sub_key"] = sub_url.rsplit("/", 1)[-1].split(".")[0]
    except Exception as exc:
        log.warning("Bilibili: failed to get WBI keys: %s", exc)

    _cache["expires"] = time.time() + 6 * 3600
    log.debug("Bilibili: WBI cache refreshed (img=%s…)", _cache["img_key"][:8])


def _sign(params: dict) -> str:
    """Return WBI-signed query string for the given params dict."""
    if time.time() >= _cache["expires"]:
        _refresh_cache()

    combined = _cache["img_key"] + _cache["sub_key"]
    mixin_key = "".join(combined[i] for i in _MIXIN_KEY_ENC_TAB)[:32]

    signed = dict(sorted({**params, "wts": str(int(time.time()))}.items()))
    query = urllib.parse.urlencode(signed)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return f"{query}&w_rid={w_rid}"


def _make_session(uid: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    session.headers["Referer"] = f"https://space.bilibili.com/{uid}/"
    if _cache["buvid3"]:
        session.cookies.set("buvid3", _cache["buvid3"], domain=".bilibili.com")
    if _cache["buvid4"]:
        session.cookies.set("buvid4", _cache["buvid4"], domain=".bilibili.com")
    return session


def fetch_bilibili(
    source_url: str,
    known_urls: set | None = None,
    date_from: str | None = None,
) -> list[dict]:
    """Fetch recent uploaded videos from a Bilibili user space.

    source_url — https://space.bilibili.com/{uid}[/upload/video]
    Returns article dicts: title, url (bilibili.com/video/BVxxx), content (description),
    published_at (ISO), needs_full_content=False.
    """
    uid = extract_uid(source_url)
    if not uid:
        log.error("Cannot extract Bilibili UID from %s", source_url)
        return []

    if date_from:
        try:
            cutoff_ts = int(
                datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc).timestamp()
            )
        except Exception:
            cutoff_ts = int(
                (datetime.now(timezone.utc) - timedelta(days=DATE_RANGE_DAYS)).timestamp()
            )
    else:
        cutoff_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=DATE_RANGE_DAYS)).timestamp()
        )

    # Ensure cache is warm before building the session
    if time.time() >= _cache["expires"]:
        _refresh_cache()

    session  = _make_session(uid)
    articles = []
    page     = 1
    skipped_known = 0

    log.info("Fetching Bilibili space uid=%s", uid)

    while len(articles) < MAX_ARTICLES_PER_SOURCE:
        try:
            signed = _sign(
                {"mid": uid, "ps": "20", "pn": str(page),
                 "order": "pubdate", "platform": "web", "tid": "0"}
            )
            r = session.get(
                f"https://api.bilibili.com/x/space/wbi/arc/search?{signed}",
                timeout=20,
            )
            if not r.content:
                log.warning("Bilibili: empty response for uid=%s page=%d", uid, page)
                break
            data = r.json()
        except Exception as exc:
            log.error("Bilibili API error uid=%s page=%d: %s", uid, page, exc)
            break

        code = data.get("code")
        if code != 0:
            log.error(
                "Bilibili API code=%d uid=%s page=%d: %s",
                code, uid, page, data.get("message", ""),
            )
            break

        vlist = (data.get("data") or {}).get("list", {}).get("vlist") or []
        if not vlist:
            break  # no more pages

        for v in vlist:
            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                break

            bvid = v.get("bvid", "")
            if not bvid:
                continue

            video_url = f"https://www.bilibili.com/video/{bvid}"
            if known_urls and video_url in known_urls:
                skipped_known += 1
                continue

            created = v.get("created", 0)
            if created and created < cutoff_ts:
                # Videos sorted newest-first — once past cutoff, stop entirely
                log.info("  Reached date cutoff at page %d", page)
                if skipped_known:
                    log.info("  Skipped %d already-known videos", skipped_known)
                log.info("  → %d videos from Bilibili uid %s", len(articles), uid)
                return articles

            pub_at = (
                datetime.utcfromtimestamp(created).replace(tzinfo=timezone.utc).isoformat()
                if created else None
            )
            articles.append({
                "title":              v.get("title", ""),
                "url":                video_url,
                "content":            v.get("description", ""),
                "published_at":       pub_at,
                "needs_full_content": False,
            })

        page += 1

    if skipped_known:
        log.info("  Skipped %d already-known videos", skipped_known)
    log.info("  → %d videos from Bilibili uid %s", len(articles), uid)
    return articles
