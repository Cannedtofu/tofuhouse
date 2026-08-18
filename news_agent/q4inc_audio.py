"""Q4 Inc event audio extraction helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = (".m3u8", ".mp3", ".m4a", ".aac", ".wav", ".mp4", ".webm")
MEDIA_HINTS = (
    ".m3u8",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".mp4",
    ".webm",
    "audio",
    "media",
    "stream",
    "archive",
    "webcast",
)


@dataclass
class Q4AudioResult:
    page_url: str
    title: str
    media_url: str
    output_path: str
    captured_urls: list[str]


def _clean_url(value: str) -> str:
    value = (value or "").strip().strip("\"'“”")
    if value.endswith("%E2%80%9D"):
        value = value[:-9]
    return value.rstrip("”")


def _safe_stem(value: str, fallback: str = "q4inc_audio") -> str:
    value = re.sub(r"[^\w.-]+", "_", value or "", flags=re.UNICODE).strip("._")
    return value[:120] or fallback


def _looks_like_media_url(url: str, content_type: str = "") -> bool:
    text = (url or "").lower()
    ctype = (content_type or "").lower()
    if any(
        marker in text
        for marker in (
            "/captions/",
            "subtitles.m3u8",
            "subtitle",
            "caption",
            "/favicon/",
            "favicon",
            "webmanifest",
            "browserconfig.xml",
            "apple-touch-icon",
            ".woff",
            ".ttf",
            ".css",
            ".js",
            ".svg",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".ico",
        )
    ):
        return False
    if any(ext in text for ext in MEDIA_EXTENSIONS):
        return True
    if ctype.startswith(("audio/", "video/")):
        return True
    if "mpegurl" in ctype or "application/vnd.apple.mpegurl" in ctype:
        return True
    return any(hint in text for hint in MEDIA_HINTS) and any(
        marker in text for marker in ("q4cdn", "q4inc", "stream", "media", "webcast")
    )


def _score_media_url(url: str) -> int:
    text = url.lower()
    if "/captions/" in text or "subtitle" in text or "caption" in text:
        return -1000
    score = 0
    if "edited-recordings" in text:
        score += 120
    if ".mp4" in text or ".webm" in text:
        score += 90
    if ".m3u8" in text:
        score += 60
    if any(ext in text for ext in (".mp3", ".m4a", ".aac", ".wav")):
        score += 70
    if any(word in text for word in ("audio", "archive", "webcast", "recording")):
        score += 20
    if any(word in text for word in ("sprite", "image", "thumbnail", ".jpg", ".png", ".css", ".js")):
        score -= 100
    return score


def _unique(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _write_debug_snapshot(page: Page, debug_dir: Path) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        (debug_dir / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        logger.exception("Could not save Q4 debug HTML")
    try:
        page.screenshot(path=str(debug_dir / "page.png"), full_page=True)
    except Exception:
        logger.exception("Could not save Q4 debug screenshot")


def _save_step(page: Page, output_dir: Path, name: str) -> None:
    try:
        step_dir = output_dir / "debug" / "steps"
        step_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(step_dir / f"{name}.png"), full_page=True)
    except Exception:
        logger.exception("Could not save Q4 step screenshot %s", name)


def _fill_first_visible(page: Page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if not loc.count():
                continue
            loc.wait_for(state="visible", timeout=1500)
            loc.click(timeout=1500)
            page.keyboard.press("Control+A")
            page.keyboard.type(value, delay=20)
            loc.dispatch_event("input")
            loc.dispatch_event("change")
            return True
        except Exception:
            continue
    return False


def _fill_input_by_dom(page: Page, value: str, kind: str) -> bool:
    try:
        return bool(page.evaluate(
            """({ value, kind }) => {
                const inputs = [...document.querySelectorAll('input')];
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const score = el => {
                    const text = [
                        el.type, el.name, el.id, el.placeholder, el.autocomplete,
                        el.getAttribute('aria-label')
                    ].filter(Boolean).join(' ').toLowerCase();
                    if (kind === 'password') {
                        if (el.type === 'password') return 100;
                        if (text.includes('password')) return 80;
                        return 0;
                    }
                    if (text.includes('email')) return 100;
                    if (text.includes('user')) return 50;
                    if (el.type === 'text') return 20;
                    return 0;
                };
                const target = inputs
                    .filter(visible)
                    .map(el => ({ el, score: score(el) }))
                    .filter(item => item.score > 0)
                    .sort((a, b) => b.score - a.score)[0]?.el;
                if (!target) return false;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(target, value);
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
                target.focus();
                return true;
            }""",
            {"value": value, "kind": kind},
        ))
    except Exception:
        return False


def _click_first_visible(page: Page, selectors: list[str], timeout_ms: int = 1200) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if not loc.count():
                continue
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.scroll_into_view_if_needed(timeout=timeout_ms)
            loc.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _submit_identity_login_once(
    page: Page,
    username: str,
    password: str,
    step_prefix: str,
    output_dir: Path,
) -> None:
    email_selectors = [
        "#email",
        "input[type='email']",
        "input[name*='email' i]",
        "input[id*='email' i]",
        "input[placeholder*='email' i]",
        "input[placeholder*='Email address' i]",
        "input[type='text']",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name*='password' i]",
        "input[id*='password' i]",
        "input[placeholder*='password' i]",
    ]
    name_selectors = [
        "input[name*='first' i]",
        "input[id*='first' i]",
        "input[placeholder*='first' i]",
    ]
    last_name_selectors = [
        "input[name*='last' i]",
        "input[id*='last' i]",
        "input[placeholder*='last' i]",
    ]
    company_selectors = [
        "input[name*='company' i]",
        "input[id*='company' i]",
        "input[placeholder*='company' i]",
    ]

    def fill_visible_form(first_name: str = "", last_name: str = "", company: str = "") -> bool:
        filled_email = (
            _fill_first_visible(page, email_selectors, username)
            or _fill_input_by_dom(page, username, "email")
        )
        filled_password = (
            _fill_first_visible(page, password_selectors, password)
            or _fill_input_by_dom(page, password, "password")
        )
        if first_name:
            _fill_first_visible(page, name_selectors, first_name)
        if last_name:
            _fill_first_visible(page, last_name_selectors, last_name)
        if company:
            _fill_first_visible(page, company_selectors, company)
        _click_first_visible(page, [
            "input[type='checkbox']",
            "label:has-text('agree')",
            "label:has-text('terms')",
        ], timeout_ms=500)
        return filled_email or filled_password

    if fill_visible_form():
        _save_step(page, output_dir, f"{step_prefix}_after_fill_login")
        _click_first_visible(page, [
            "button:has-text('Next')",
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Continue')",
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "button:has-text('Register')",
            "button:has-text('Submit')",
            "text=/continue/i",
        ])
        page.keyboard.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=7000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(1)

    if _fill_input_by_dom(page, password, "password") or _fill_first_visible(page, password_selectors, password):
        _save_step(page, output_dir, f"{step_prefix}_after_fill_password")
        _click_first_visible(page, [
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "button[type='submit']",
            "button:has-text('Continue')",
        ])
        page.keyboard.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=9000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(1)


def _try_login_or_register(
    page: Page,
    username: str,
    password: str,
    output_dir: Path,
    allow_guest_registration: bool = False,
    first_name: str = "",
    last_name: str = "",
    company: str = "",
) -> None:
    for _ in range(3):
        clicked_entry = _click_first_visible(page, [
            "button:has-text('Register with a Q4 Account')",
            "text='Register with a Q4 Account'",
            "button:has-text('Log in with a Q4 Account')",
            "button:has-text('Login with a Q4 Account')",
            "text=/sign\\s*in/i",
            "text=/log\\s*in/i",
            "text=/already registered/i",
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
        ])
        if clicked_entry:
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(1)

        _submit_identity_login_once(page, username, password, "initial", output_dir)
        time.sleep(1.5)

    if allow_guest_registration:
        logger.info("Guest registration fallback is disabled during Q4 account login debugging.")


def _try_start_playback(page: Page) -> None:
    selectors = [
        "button:has-text('Play')",
        "button[aria-label*='play' i]",
        "text=/listen/i",
        "text=/webcast/i",
        "text=/archive/i",
        "text=/on demand/i",
        "audio",
        "video",
    ]
    for _ in range(3):
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() and loc.is_visible(timeout=700):
                    loc.click()
                    time.sleep(1.5)
            except Exception:
                continue
        try:
            page.evaluate(
                """() => {
                    for (const el of [...document.querySelectorAll('audio,video')]) {
                        try { el.muted = true; el.play && el.play(); } catch (e) {}
                    }
                }"""
            )
        except Exception:
            pass


def _recover_attendee_loading(page: Page, attendee_url: str) -> None:
    for _ in range(3):
        try:
            body_text = page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            body_text = ""
        if "loading" not in body_text or urlparse(page.url).netloc == "identity.q4inc.com":
            return
        if "events.q4inc.com/attendee" not in page.url:
            return
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=12000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(3)
        try:
            body_text = page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            body_text = ""
        if "loading" not in body_text:
            return
        try:
            page.goto(attendee_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=12000)
        except PlaywrightTimeoutError:
            pass
        time.sleep(3)


def _goto_with_retries(page: Page, url: str, timeout_ms: int, attempts: int = 3) -> None:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(2 * attempt)
    raise last_error


def _complete_identity_challenges(page: Page, username: str, password: str, output_dir: Path) -> None:
    for index in range(4):
        if urlparse(page.url).netloc != "identity.q4inc.com":
            return
        logger.info("Completing Q4 identity challenge round %s at %s", index + 1, page.url)
        _submit_identity_login_once(page, username, password, f"identity_{index + 1}", output_dir)
        time.sleep(2)


def _extract_dom_media_urls(page: Page) -> list[str]:
    try:
        urls = page.evaluate(
            """() => {
                const values = [];
                const push = value => {
                    if (typeof value === 'string' && /^https?:/i.test(value)) values.push(value);
                };
                for (const el of [...document.querySelectorAll('audio,video,source,a')]) {
                    push(el.currentSrc);
                    push(el.src);
                    push(el.href);
                }
                const html = document.documentElement.innerHTML;
                const re = /https?:\\/\\/[^"'<>\\s]+/g;
                for (const match of html.matchAll(re)) push(match[0]);
                return values;
            }"""
        )
        return _unique(urls)
    except Exception:
        logger.exception("Could not inspect Q4 DOM media URLs")
        return []


def _download_with_requests(url: str, output_path: Path) -> None:
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, headers=headers, stream=True, timeout=60) as response:
        response.raise_for_status()
        with output_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def _download_with_external_tool(url: str, output_path: Path) -> None:
    ffmpeg_path = _find_ffmpeg()
    if shutil.which("yt-dlp"):
        cmd = ["yt-dlp", "--no-playlist", "-o", str(output_path), url]
    elif ffmpeg_path:
        cmd = [ffmpeg_path, "-y", "-i", url, "-vn", "-c", "copy", str(output_path)]
    else:
        raise RuntimeError("Need yt-dlp or ffmpeg to download HLS media.")
    subprocess.run(cmd, check=True)


def _find_ffmpeg() -> str | None:
    imageio_ffmpeg_path = None
    try:
        import imageio_ffmpeg
        imageio_ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        imageio_ffmpeg_path = None

    candidates = [
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
        os.environ.get("FFMPEG_PATH"),
        imageio_ffmpeg_path,
        r"C:\Users\Jason\AppData\Local\ms-playwright\ffmpeg-1011\ffmpeg-win64.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _extract_audio_from_video(video_path: Path, audio_path: Path) -> None:
    ffmpeg_path = _find_ffmpeg()
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is required to extract audio from Q4 mp4 recordings.")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-c:a",
        "copy",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True)


def _download_media(url: str, output_dir: Path, title: str) -> Path:
    parsed_path = urlparse(url).path.lower()
    is_hls = ".m3u8" in parsed_path or ".m3u8" in url.lower()
    is_video = any(ext in parsed_path for ext in (".mp4", ".webm", ".mov", ".mkv"))
    ext = ".m4a" if is_hls or is_video else os.path.splitext(parsed_path)[1]
    if not ext or len(ext) > 8:
        ext = ".mp3"
    output_path = output_dir / f"{_safe_stem(title)}{ext}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if is_hls:
        _download_with_external_tool(url, output_path)
    elif is_video:
        video_ext = os.path.splitext(parsed_path)[1] or ".mp4"
        tmp_video_path = output_dir / f"{_safe_stem(title)}.tmp{video_ext}"
        _download_with_requests(url, tmp_video_path)
        try:
            _extract_audio_from_video(tmp_video_path, output_path)
        finally:
            try:
                tmp_video_path.unlink()
            except OSError:
                pass
    else:
        _download_with_requests(url, output_path)
    return output_path


def extract_q4inc_audio(
    attendee_url: str,
    output_dir: str | os.PathLike = "audio_cache/q4inc",
    headless: bool = True,
    timeout_ms: int = 90000,
    allow_guest_registration: bool = False,
    slow_mo_ms: int = 0,
    hold_seconds: int = 0,
) -> Q4AudioResult:
    """Log into a Q4 attendee page, capture media URLs, and save the best audio."""
    load_dotenv()
    username = os.environ.get("Q4I_USERNAME", "").strip()
    password = os.environ.get("Q4I_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("Q4I_USERNAME and Q4I_PASSWORD must be set in .env")

    attendee_url = _clean_url(attendee_url)
    output_dir = Path(output_dir)
    debug_dir = output_dir / "debug"
    captured: list[str] = []
    network_debug: list[dict] = []
    console_debug: list[str] = []

    with sync_playwright() as p:
        executable_path = (
            os.environ.get("Q4I_BROWSER_PATH")
            or shutil.which("chrome")
            or shutil.which("chrome.exe")
            or shutil.which("msedge")
            or shutil.which("msedge.exe")
        )
        for candidate in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"D:\爬虫用chrome\chrome-win\chrome.exe",
        ):
            if not executable_path and os.path.exists(candidate):
                executable_path = candidate
        launch_kwargs = {"headless": headless}
        if slow_mo_ms:
            launch_kwargs["slow_mo"] = slow_mo_ms
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
        )
        page = context.new_page()

        def on_response(response):
            try:
                ctype = response.headers.get("content-type", "")
                rtype = response.request.resource_type
                if _looks_like_media_url(response.url, ctype) or response.status >= 400 or rtype in ("xhr", "fetch"):
                    network_debug.append({
                        "type": "response",
                        "status": response.status,
                        "url": response.url,
                        "content_type": ctype,
                        "resource_type": rtype,
                    })
                if _looks_like_media_url(response.url, ctype):
                    captured.append(response.url)
            except Exception:
                pass

        page.on("response", on_response)
        page.on("requestfailed", lambda request: network_debug.append({
            "type": "requestfailed",
            "url": request.url,
            "failure": request.failure,
        }))
        page.on("console", lambda message: console_debug.append(f"{message.type}: {message.text}"))
        _goto_with_retries(page, attendee_url, timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except PlaywrightTimeoutError:
            pass

        _try_login_or_register(
            page,
            username,
            password,
            output_dir,
            allow_guest_registration=allow_guest_registration,
            first_name=os.environ.get("Q4I_FIRST_NAME", "").strip(),
            last_name=os.environ.get("Q4I_LAST_NAME", "").strip(),
            company=os.environ.get("Q4I_COMPANY", "").strip(),
        )
        _complete_identity_challenges(page, username, password, output_dir)
        _recover_attendee_loading(page, attendee_url)
        _complete_identity_challenges(page, username, password, output_dir)
        _recover_attendee_loading(page, attendee_url)
        _try_start_playback(page)
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline and not any(_score_media_url(url) > 40 for url in captured):
            _try_start_playback(page)
            captured.extend(_extract_dom_media_urls(page))
            captured = _unique(captured)
            time.sleep(2)

        captured.extend(_extract_dom_media_urls(page))
        captured = _unique(captured)
        _write_debug_snapshot(page, debug_dir)
        title = page.title() or "Q4 Inc webcast"
        (debug_dir / "debug.json").write_text(
            json.dumps({
                "final_url": page.url,
                "title": title,
                "network": network_debug[-200:],
                "console": console_debug[-200:],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (debug_dir / "captured_urls.json").write_text(
            json.dumps(captured, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if hold_seconds:
            logger.info("Holding Q4 browser open for %s seconds", hold_seconds)
            time.sleep(hold_seconds)
        browser.close()

    candidates = sorted(
        [url for url in captured if _looks_like_media_url(url)],
        key=_score_media_url,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No Q4 media URL captured. Debug snapshot saved in {debug_dir}")

    media_url = candidates[0]
    output_path = _download_media(media_url, output_dir, title)
    return Q4AudioResult(
        page_url=attendee_url,
        title=title,
        media_url=media_url,
        output_path=str(output_path),
        captured_urls=candidates,
    )
