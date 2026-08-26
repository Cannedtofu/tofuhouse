"""
Background worker for YouTube transcript extraction and summarization.

Modes:
  no_diarization — try yt-dlp captions first; if none, offer audio fallback (user-approved)
  diarization    — always use audio download + paraformer-v2 with speaker diarization

Workflow per mode:
  process_transcript_job()        First stage — caption try or immediate audio start
  continue_audio_transcript()     Called after user approves audio fallback (no_diarization only)
  generate_transcript_summary()   Called after user clicks "Generate AI Summary"

ASR: paraformer-v2 via DashScope Recognition API
  - Full audio file sent in one call (supports ≤2 GB / ≤12 h; diarization recommended ≤2 h)
  - Without diarization: plain text output
  - With diarization:    sentence_info with speaker_id, formatted as [HH:MM:SS] [Speaker A] lines
                         (timestamp = start of that speaker turn); consistent speaker IDs
                         throughout the entire video

Captions (yt-dlp VTT / youtube-transcript-api): cues are grouped into paragraphs at
~30-second intervals, each prefixed with a [HH:MM:SS] marker (per-cue timestamps would
be too dense — cues arrive every few seconds).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time

import db
import secrets

import audio_registry
from config import (
    APP_BASE_URL,
    ASR_MODEL,
    AUDIO_CACHE_DIR,
    QWEN_API_KEY,
    QWEN_BASE_URL,
    QWEN_SUMMARY_MODEL,
    QWEN_TRANSLATION_MODEL,
    SOCKS_PROXY,
    YOUTUBE_COOKIES_FILE,
)

logger = logging.getLogger(__name__)

# Chunk sizes for summarization, calibrated to qwen-plus's 131k-token context window.
# English: ~4 chars/token → 80k chars ≈ 20k tokens, plenty of headroom.
# Chinese: ~1-1.5 chars/token → 40k chars ≈ 27-40k tokens, still safe with output budget.
# Chunks are split evenly (N = ceil(total / max_chars)) so no lopsided remainder occurs.
_MAX_CHARS_EN = 80_000
_MAX_CHARS_ZH = 40_000
_CHUNK_OVERLAP = 800   # raw chars from tail of previous chunk fed into next chunk for continuity


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Strip whitespace and remove known tracking/sharing parameters that don't affect the video.

    Handles:
    - ``?si=...``       — YouTube sharing token (youtu.be and regular watch URLs)
    - ``?feature=...``  — YouTube source tracking
    - ``&pp=...``       — YouTube homepage placement param
    The video ID and ``t``/``start`` timestamp params are preserved.
    """
    url = url.strip()
    # Parse out and drop known-irrelevant query params
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    _STRIP_PARAMS = {"si", "feature", "pp", "utm_source", "utm_medium", "utm_campaign"}
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k.lower() not in _STRIP_PARAMS}
        new_query = urlencode(cleaned, doseq=True)
        url = urlunparse(parsed._replace(query=new_query))
    except Exception:
        pass  # if parsing fails, return the stripped original
    return url


def extract_video_id(url: str) -> str | None:
    url = normalize_url(url)
    patterns = [
        r"(?:youtube\.com/watch\?(?:[^&]*&)*v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/v/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def is_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None


_XIAOYUZHOU_EPISODE_RE = re.compile(r"xiaoyuzhoufm\.com/episode/([a-f0-9]+)", re.I)
_BILIBILI_VIDEO_RE     = re.compile(r"bilibili\.com/video/(BV[a-zA-Z0-9]+)", re.I)


def extract_xiaoyuzhou_episode_id(url: str) -> str | None:
    m = _XIAOYUZHOU_EPISODE_RE.search(url)
    return m.group(1) if m else None


def is_xiaoyuzhou_url(url: str) -> bool:
    return extract_xiaoyuzhou_episode_id(url) is not None


def extract_bilibili_video_id(url: str) -> str | None:
    """Return the BV ID from a bilibili.com/video/BVxxx URL."""
    m = _BILIBILI_VIDEO_RE.search(url)
    return m.group(1) if m else None


def is_bilibili_url(url: str) -> bool:
    return extract_bilibili_video_id(url) is not None


# ---------------------------------------------------------------------------
# Fast path — yt-dlp subtitle download (caption file only, no audio)
# ---------------------------------------------------------------------------

# Captions arrive as cues every few seconds — too dense to stamp individually.
# Cues are grouped into paragraphs spanning roughly this many seconds, each
# prefixed with a single [HH:MM:SS] marker.
_CAPTION_PARAGRAPH_INTERVAL_SEC = 30


def _is_transient_ytdlp_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "eof occurred in violation of protocol",
            "ssl",
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "remote end closed",
            "temporarily unavailable",
        )
    )


def _run_ytdlp_with_outer_retries(action: str, fn, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not _is_transient_ytdlp_error(exc):
                raise
            sleep_seconds = min(2 ** attempt, 8)
            logger.warning(
                "yt-dlp %s failed with transient network error (attempt %d/%d): %s; retrying in %ss",
                action, attempt, attempts, exc, sleep_seconds,
            )
            time.sleep(sleep_seconds)


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _format_captions_with_timestamps(cues: list[tuple[float, str]]) -> str:
    """Group caption cues into paragraphs, marking each with a [HH:MM:SS] timestamp
    at the start of every ~_CAPTION_PARAGRAPH_INTERVAL_SEC-second span."""
    if not cues:
        return ""
    paragraphs: list[str] = []
    para_start = cues[0][0]
    para_parts: list[str] = []
    for start, text in cues:
        if para_parts and (start - para_start) >= _CAPTION_PARAGRAPH_INTERVAL_SEC:
            paragraphs.append(f"[{_format_timestamp(para_start)}] {' '.join(para_parts)}")
            para_start = start
            para_parts = [text]
        else:
            para_parts.append(text)
    if para_parts:
        paragraphs.append(f"[{_format_timestamp(para_start)}] {' '.join(para_parts)}")
    return "\n\n".join(paragraphs)


def _parse_vtt_cues(content: str) -> list[tuple[float, str]]:
    """Parse WebVTT into (start_seconds, text) cues, deduping consecutive identical
    lines (YouTube auto-captions repeat the rolling line in karaoke-style overlap)."""
    content = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", content)
    content = re.sub(r"<[^>]+>", "", content)

    cues: list[tuple[float, str]] = []
    prev_text = None
    current_start: float = 0.0
    current_lines: list[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->", line)
        if m:
            if current_lines:
                text = " ".join(current_lines)
                if text != prev_text:
                    cues.append((current_start, text))
                    prev_text = text
            h, mnt, sec, ms = map(int, m.groups())
            current_start = h * 3600 + mnt * 60 + sec + ms / 1000
            current_lines = []
            continue
        if (line.startswith("WEBVTT")
                or re.match(r"^\d+$", line)
                or re.match(r"^[A-Z][a-z]+: ", line)):
            continue
        current_lines.append(line)

    if current_lines:
        text = " ".join(current_lines)
        if text != prev_text:
            cues.append((current_start, text))

    return cues


def _parse_vtt(content: str) -> str:
    """Convert WebVTT (including YouTube karaoke-style) to timestamped paragraphs."""
    return _format_captions_with_timestamps(_parse_vtt_cues(content))


def _fetch_via_transcript_api(video_id: str) -> str | None:
    """
    Try youtube-transcript-api as a no-auth fast path for YouTube captions.
    Uses YouTube's internal transcript endpoint — not blocked on cloud IPs.
    Returns plain text or None if no transcript is available.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        kwargs = {}
        if SOCKS_PROXY:
            kwargs["proxies"] = {"https": SOCKS_PROXY, "http": SOCKS_PROXY}
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
        preferred = ["en", "en-US", "en-GB", "en-AU",
                     "zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-HK"]
        transcript = None
        try:
            transcript = transcript_list.find_transcript(preferred)
        except NoTranscriptFound:
            for t in transcript_list:
                transcript = t
                break
        if transcript is None:
            logger.info("youtube-transcript-api: no transcript for %s", video_id)
            return None
        data = transcript.fetch()
        cues = [(item["start"], item["text"].strip()) for item in data if item.get("text", "").strip()]
        text = _format_captions_with_timestamps(cues)
        if text:
            logger.info("youtube-transcript-api: got transcript for %s (%d chars, lang=%s)",
                        video_id, len(text), transcript.language_code)
        return text or None
    except Exception as exc:
        logger.warning("youtube-transcript-api failed for %s: %s", video_id, exc)
        return None


def _fetch_transcript_fast(video_id_or_url: str) -> str | None:
    """
    Probe available subtitles. For YouTube video IDs, tries youtube-transcript-api
    first (no auth needed), then falls back to yt-dlp. For full URLs (Bilibili etc.)
    goes straight to yt-dlp.
    Returns plain text or None to trigger audio fallback.
    """
    import yt_dlp

    # YouTube bare video IDs: try the no-auth API path first
    if not video_id_or_url.startswith("http"):
        result = _fetch_via_transcript_api(video_id_or_url)
        if result is not None:
            return result
        logger.info("youtube-transcript-api returned None for %s — falling back to yt-dlp", video_id_or_url)

    # Short alias used throughout log messages (avoids NameError — parameter is video_id_or_url)
    video_id = video_id_or_url

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 5,
        "extractor_retries": 5,
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        ydl_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        ydl_opts["proxy"] = SOCKS_PROXY
    else:
        logger.warning("yt-dlp subtitle: no proxy configured — may be blocked on cloud IPs")

    url = (
        video_id_or_url
        if video_id_or_url.startswith("http")
        else f"https://www.youtube.com/watch?v={video_id_or_url}"
    )

    # Step 1: probe what subtitle tracks actually exist
    try:
        info = _run_ytdlp_with_outer_retries(
            f"subtitle extract_info for {video_id}",
            lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False),
        )
    except Exception as exc:
        logger.warning("yt-dlp extract_info failed for %s: %s", video_id, exc)
        return None

    subtitles = info.get("subtitles") or {}
    auto_caps  = info.get("automatic_captions") or {}
    logger.info(
        "Subtitle tracks for %s — manual: %s  auto: %s",
        video_id, sorted(subtitles.keys()), sorted(auto_caps.keys()),
    )

    def _pick_vtt(pool: dict, langs: list[str]) -> tuple[str | None, str | None]:
        """Return (url, lang) for the first matching VTT track."""
        for lang in langs:
            for fmt in pool.get(lang, []):
                if fmt.get("ext") == "vtt":
                    return fmt["url"], lang
        return None, None

    preferred = ["en", "en-US", "en-GB", "en-AU", "en-orig",
                 "zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-HK"]

    sub_url, sub_lang = _pick_vtt(subtitles, preferred)          # manual first
    if not sub_url:
        sub_url, sub_lang = _pick_vtt(auto_caps, preferred)      # then auto-generated
    if not sub_url:
        # fall back to any language that has a VTT track
        for pool in (subtitles, auto_caps):
            for lang, fmts in pool.items():
                for fmt in fmts:
                    if fmt.get("ext") == "vtt":
                        sub_url, sub_lang = fmt["url"], lang
                        break
                if sub_url:
                    break
            if sub_url:
                break

    if not sub_url:
        logger.info("No VTT subtitle track available for %s", video_id)
        return None

    # Step 2: download the VTT through yt-dlp's networking (proxy-aware)
    logger.info("Downloading subtitle for %s (lang=%s)", video_id, sub_lang)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            resp = ydl.urlopen(sub_url)
            vtt_content = resp.read().decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to fetch subtitle VTT for %s: %s", video_id, exc)
        return None

    text = _parse_vtt(vtt_content)
    if text:
        logger.info("Subtitle extracted for %s (%d chars)", video_id, len(text))
        return text

    logger.warning("Subtitle for %s parsed to empty string (raw %d chars)", video_id, len(vtt_content))
    return None


# ---------------------------------------------------------------------------
# Audio download — yt-dlp
# ---------------------------------------------------------------------------

def _download_audio(video_id_or_url: str, tmp_dir: str) -> str:
    """Download audio for a YouTube video ID or a full URL (Bilibili, etc.)."""
    import yt_dlp

    if video_id_or_url.startswith("http"):
        dl_url  = video_id_or_url
        file_id = re.sub(r"[^a-zA-Z0-9_-]", "_", video_id_or_url.split("/")[-1])[:40]
    else:
        dl_url  = f"https://www.youtube.com/watch?v={video_id_or_url}"
        file_id = video_id_or_url

    output_template = os.path.join(tmp_dir, f"{file_id}.%(ext)s")
    ydl_opts = {
        # Prefer native m4a (no ffmpeg needed); fall back to any audio stream.
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "retry_sleep_functions": {"http": lambda n: min(4 ** n, 60)},
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        ydl_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        logger.info("yt-dlp: using cookies file %s", YOUTUBE_COOKIES_FILE)
    if SOCKS_PROXY:
        ydl_opts["proxy"] = SOCKS_PROXY
        logger.info("yt-dlp: routing through proxy %s", SOCKS_PROXY)
    else:
        logger.warning("yt-dlp: no proxy configured — may be blocked on cloud IPs")

    _run_ytdlp_with_outer_retries(
        f"audio download for {file_id}",
        lambda: yt_dlp.YoutubeDL(ydl_opts).download([dl_url]),
    )

    for fname in os.listdir(tmp_dir):
        if fname.startswith(file_id):
            return os.path.join(tmp_dir, fname)

    raise FileNotFoundError(f"yt-dlp produced no output file in {tmp_dir}")


# ---------------------------------------------------------------------------
# ASR — paraformer-v2 via DashScope Recognition API
# ---------------------------------------------------------------------------

def _speaker_label(speaker_id) -> str:
    letters = "ABCDEFGHIJ"
    if speaker_id is None:
        return "Speaker"
    return f"Speaker {letters[speaker_id]}" if speaker_id < len(letters) else f"Speaker {speaker_id + 1}"


def _format_diarized_sentences(sentences: list) -> str:
    """Group consecutive sentences by speaker_id and format as labeled paragraphs,
    each prefixed with the speaker turn's start time: [01:23] [Speaker A]."""
    lines: list[str] = []
    current_speaker = None
    current_parts: list[str] = []
    turn_start_ms = None

    def _flush():
        if not current_parts:
            return
        ts = f"[{_format_timestamp(turn_start_ms / 1000)}] " if turn_start_ms is not None else ""
        lines.append(f"{ts}[{_speaker_label(current_speaker)}] {' '.join(current_parts)}")

    for s in sentences:
        spk  = s.get("speaker_id")
        text = s.get("text", "").strip()
        if not text:
            continue
        if spk != current_speaker:
            _flush()
            current_speaker = spk
            current_parts   = [text]
            turn_start_ms   = s.get("begin_time")
        else:
            current_parts.append(text)

    _flush()
    return "\n".join(lines)


def _transcribe_audio_file(audio_path: str, diarize: bool = False) -> str:
    """
    Transcribe a full audio file with paraformer-v2 via DashScope Transcription API.

    DashScope is a cloud service and cannot access local file:// paths. We register
    the audio file under a random token and serve it over HTTP from this app for the
    duration of the transcription call, then unregister it.
    """
    import json
    import urllib.request

    import dashscope
    from dashscope.audio.asr import Transcription

    dashscope.api_key = QWEN_API_KEY

    token = secrets.token_urlsafe(32)
    audio_registry.register(token, audio_path)
    public_url = f"{APP_BASE_URL}/transcript/temp-audio/{token}"
    logger.info("Registered audio for DashScope at %s…/temp-audio/<token>", APP_BASE_URL)

    try:
        call_kwargs: dict = {
            "model": ASR_MODEL,
            "file_urls": [public_url],
            "language_hints": ["zh", "en"],
        }
        if diarize:
            call_kwargs["diarization_enabled"] = True

        task_resp = Transcription.async_call(**call_kwargs)
        if task_resp.status_code != 200:
            raise RuntimeError(
                f"Transcription submit error {task_resp.status_code}: {task_resp.message}"
            )

        resp = Transcription.wait(task_resp)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Transcription error {resp.status_code}: {resp.message}"
            )

        results = (resp.output or {}).get("results") or []
        if not results:
            raise RuntimeError("Transcription returned no results")

        result = results[0]
        if result.get("subtask_status") != "SUCCEEDED":
            raise RuntimeError(
                f"Transcription subtask FAILED: {result.get('message') or result.get('subtask_status')}"
            )

        trans_url = result.get("transcription_url")
        if not trans_url:
            raise RuntimeError("No transcription_url in result")

        with urllib.request.urlopen(trans_url) as f:
            trans_data = json.loads(f.read().decode("utf-8"))

        transcripts = trans_data.get("transcripts") or []
        if not transcripts:
            return trans_data.get("text", "")

        channel   = transcripts[0]
        sentences = channel.get("sentences") or []

        if not sentences:
            return channel.get("text", "")

        if diarize:
            return _format_diarized_sentences(sentences)

        return " ".join(s.get("text", "") for s in sentences)

    finally:
        audio_registry.unregister(token)


def _transcribe_url_direct(audio_url: str, diarize: bool = False) -> str:
    """Transcribe audio from a public URL directly via DashScope.

    Unlike _transcribe_audio_file, no local download or temp serving is needed —
    DashScope pulls the audio directly from the supplied URL. Used for Xiaoyuzhou
    episodes where the audio is already at a public xyzcdn.net URL.
    """
    import json
    import urllib.request

    import dashscope
    from dashscope.audio.asr import Transcription

    dashscope.api_key = QWEN_API_KEY
    logger.info("Transcribing direct URL with paraformer-v2 (diarize=%s): %s", diarize, audio_url[:80])

    call_kwargs: dict = {
        "model": ASR_MODEL,
        "file_urls": [audio_url],
        "language_hints": ["zh", "en"],
    }
    if diarize:
        call_kwargs["diarization_enabled"] = True

    task_resp = Transcription.async_call(**call_kwargs)
    if task_resp.status_code != 200:
        raise RuntimeError(
            f"Transcription submit error {task_resp.status_code}: {task_resp.message}"
        )

    resp = Transcription.wait(task_resp)
    if resp.status_code != 200:
        raise RuntimeError(f"Transcription error {resp.status_code}: {resp.message}")

    results = (resp.output or {}).get("results") or []
    if not results:
        raise RuntimeError("Transcription returned no results")

    result = results[0]
    if result.get("subtask_status") != "SUCCEEDED":
        raise RuntimeError(
            f"Transcription subtask FAILED: {result.get('message') or result.get('subtask_status')}"
        )

    trans_url = result.get("transcription_url")
    if not trans_url:
        raise RuntimeError("No transcription_url in result")

    with urllib.request.urlopen(trans_url) as f:
        trans_data = json.loads(f.read().decode("utf-8"))

    transcripts = trans_data.get("transcripts") or []
    if not transcripts:
        return trans_data.get("text", "")

    channel   = transcripts[0]
    sentences = channel.get("sentences") or []
    if not sentences:
        return channel.get("text", "")

    if diarize:
        return _format_diarized_sentences(sentences)
    return " ".join(s.get("text", "") for s in sentences)


def _run_url_audio_transcript(job_id: str, audio_url: str, diarize: bool = False) -> None:
    """Transcribe a Xiaoyuzhou episode from its direct public audio URL."""
    logger.info("Transcribing Xiaoyuzhou audio for job %s (diarize=%s)", job_id, diarize)
    transcript = _transcribe_url_direct(audio_url, diarize=diarize)
    db.update_transcript_job(job_id, status="done", transcript=transcript)
    logger.info("Xiaoyuzhou transcript job %s done (%d chars)", job_id, len(transcript))


# ---------------------------------------------------------------------------
# Xiaoyuzhou metadata
# ---------------------------------------------------------------------------

def _fetch_xiaoyuzhou_metadata(episode_id: str) -> dict:
    """Fetch title, author and audio URL for a Xiaoyuzhou episode. Non-fatal."""
    try:
        from fetchers.xiaoyuzhou import get_episode_metadata
        meta = get_episode_metadata(episode_id)
        return meta or {}
    except Exception as exc:
        logger.warning("Could not fetch Xiaoyuzhou metadata for %s: %s", episode_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Video metadata
# ---------------------------------------------------------------------------

def _fetch_video_metadata(video_id_or_url: str) -> tuple[str | None, str | None]:
    """Return (title, uploader) for a YouTube or Bilibili video. Non-fatal."""
    import yt_dlp

    url = (
        video_id_or_url
        if video_id_or_url.startswith("http")
        else f"https://www.youtube.com/watch?v={video_id_or_url}"
    )

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 5,
        "extractor_retries": 5,
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        opts["proxy"] = SOCKS_PROXY

    try:
        info = _run_ytdlp_with_outer_retries(
            f"metadata extract_info for {video_id_or_url}",
            lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False),
        )
        title  = info.get("title")
        author = info.get("uploader") or info.get("channel")
        return title, author
    except Exception as exc:
        logger.warning("Could not fetch metadata for %s: %s", video_id_or_url, exc)
        return None, None


# ---------------------------------------------------------------------------
# Shared audio pipeline helper
# ---------------------------------------------------------------------------

def _persist_audio(job_id: str, src_path: str) -> str:
    """Move downloaded audio to a persistent cache dir and return the new path."""
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    ext = os.path.splitext(src_path)[1] or ".m4a"
    dest = os.path.join(AUDIO_CACHE_DIR, f"{job_id}{ext}")
    shutil.move(src_path, dest)
    return dest


def _run_local_media_transcript(
    job_id: str,
    media_path: str,
    diarize: bool = False,
) -> None:
    """Transcribe an already-uploaded local media file and clean it up on success."""
    db.update_transcript_job(job_id, status="processing", audio_path=media_path)
    logger.info("Transcribing uploaded media for job %s (diarize=%s): %s", job_id, diarize, media_path)
    transcript = _transcribe_audio_file(media_path, diarize=diarize)

    db.update_transcript_job(job_id, status="done", transcript=transcript)
    logger.info("Uploaded media transcript job %s done (%d chars)", job_id, len(transcript))

    try:
        os.remove(media_path)
        logger.info("Deleted uploaded media cache %s after successful transcription", media_path)
    except OSError:
        pass


def _run_audio_transcript(
    job_id: str,
    video_id: str,
    diarize: bool = False,
    existing_audio_path: str | None = None,
) -> None:
    """
    Download (or reuse cached) audio, transcribe with paraformer-v2, store transcript.

    Audio is moved to AUDIO_CACHE_DIR immediately after download so it survives a
    transcription failure. It is deleted only after successful transcription.
    Pass existing_audio_path to skip the download step (retry flow).
    """
    if existing_audio_path and os.path.isfile(existing_audio_path):
        audio_path = existing_audio_path
        logger.info("Reusing cached audio at %s for job %s", audio_path, job_id)
    else:
        tmp_dir = tempfile.mkdtemp(prefix="transcript_")
        try:
            raw_path = _download_audio(video_id, tmp_dir)
            logger.info("Audio downloaded to %s", raw_path)
            audio_path = _persist_audio(job_id, raw_path)
            logger.info("Audio persisted to %s", audio_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        db.update_transcript_job(job_id, status="processing", audio_path=audio_path)

    logger.info("Transcribing full audio with paraformer-v2 (diarize=%s)", diarize)
    transcript = _transcribe_audio_file(audio_path, diarize=diarize)

    db.update_transcript_job(job_id, status="done", transcript=transcript)
    logger.info("Audio transcript job %s done (%d chars)", job_id, len(transcript))

    try:
        os.remove(audio_path)
        logger.info("Deleted cached audio %s after successful transcription", audio_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Summarization — Qwen via DashScope
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Summarization prompts ? shared pipeline for English/Chinese transcripts
#
# Step 1: rewrite the transcript in speech order, making each turn more
# efficient without losing factual details, opinions, examples, or evidence.
# Step 2: summarize the step-1 result into:
#   - 议题与观点
#   - 关键论据与证据
#   - 分歧与开放性问题
#   - åæ­§ä¸å¼æ¾æ§é®é¢
#
# Both steps preserve chunking for long transcripts. The final summary output
# contains step 2 first, followed by the full step-1 result.
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = (
    "你是一位严谨的中文分析编辑。输入可能是英文或中文的逐字稿，输出必须是简体中文。"
    "你的首要原则是信息零丢失：不得遗漏任何事实、观点、论据、例子、轶事、限定条件、"
    "保留意见、时间顺序或说话人归属。你可以压缩表达，但不能减少信息。"
    "如果原文里有英文人名、机构名、产品名、技术术语，请保留英文原文；若中文译名已非常通用，可在后面补充括号。"
)

_STEP1_SINGLE_PROMPT = """\
请处理下面这份逐字稿，生成“第一步：按发言顺序整理”。

目标：
1. 严格按照原始发言顺序输出，不能重排。
2. 每当有人说话时，用更高效、更清晰的中文重述这段发言，但不得丢失任何事实细节、观点、例子、轶事、数字、限定条件或语气上的保留。
3. 如果该段内容足够丰富，请在该发言人条目下区分“主张/观点”和“证据/例子/轶事”。
4. 如果该段内容较短，就直接高效重述，不必强行拆分小项。
5. 如原文有说话人标签、姓名或时间戳，尽量保留；如果没有，不要虚构。
6. 不要进入议题归纳，不要跨段整合，只做按顺序的信息压缩整理。

输出格式：
- 只输出“第一步：按发言顺序整理”的正文内容。
- 按发言顺序逐段书写。
- 不要添加前言、结语或解释。

逐字稿：
{transcript}"""

_STEP1_CHUNK_PROMPT = """\
下面是逐字稿的第 {part} / {total} 部分。请只处理这一部分，并输出属于这一部分的“第一步：按发言顺序整理”。{context_block}

要求：
1. 严格保持这一部分内部的发言顺序，不得重排。
2. 对每段发言做高效中文重述，但不得丢失任何事实细节、观点、例子、轶事、数字、限定条件或语气上的保留。
3. 内容丰富时，可在该发言人条目下区分“主张/观点”和“证据/例子/轶事”；内容简短时直接高效重述即可。
4. 如原文有说话人标签、姓名或时间戳，尽量保留；如果没有，不要虚构。
5. 不要做议题汇总，不要总结全篇，不要补写这一部分之外的信息。
6. 若上文片段仅用于衔接参考，不要重复输出它。

只输出这一部分整理后的正文：
{transcript}"""

_STEP2_SINGLE_PROMPT = """\
下面是一份已经完成“第一步：按发言顺序整理”的材料。请基于它生成“第二步：主题化重组笔记”。

你的任务不是摘要，而是重组：把第一步材料中的实质信息按话题重新排列。不要主动压缩、筛选或提炼成短结论。

要求：
1. 只基于提供的第一步材料，不要引入材料中没有的信息。
2. 按话题建立多个主题。每个主题下收录 Step 1 中与该主题相关的全部实质信息。
3. 不要判断信息是否“重要”；只要包含事实、观点、例子、数字、经历、引用、类比、因果解释、限定条件、反驳、保留意见、分歧或开放问题，就应被保留。
4. 可以删除口癖、寒暄、空泛重复和完全重复表达；不得删除具体细节。
5. 可以合并完全重复的信息；不得合并只是主题相近但细节、角度、例子、限定条件或说话人立场不同的信息。
6. 每个主题内部尽量保留该主题相关讨论的推进顺序：提出、解释、举例、补充、反驳、修正、保留意见。
7. 尽量保留说话人归属、时间戳、人名、机构名、产品名、数字和限定条件。
8. 如果某条实质信息难以归入已有主题，请新建主题，不要省略。
9. 输出应自然可读，但不要为了简洁牺牲信息完整性。

输出格式：
- 使用 Markdown。
- 每个主题用 `## 主题：...`。
- 每个主题下使用 `【相关发言与信息】`。
- 如该主题包含分歧、保留意见或开放问题，在同一主题下添加 `【分歧、保留与开放问题】`。
- 不要添加总摘要、前言或结语。

第一步材料：
{notes}"""

_STEP2_CHUNK_PROMPT = """\
下面是“第一步：按发言顺序整理”材料的第 {part} / {total} 部分。请将这一部分转换为可用于最终合并的“第二步：主题化重组笔记”片段。{context_block}

你的任务不是摘要，而是重组：把这一部分中的实质信息按话题重新排列。不要主动压缩、筛选或提炼成短结论。

要求：
1. 只处理这一部分材料里的信息，不要补写其他部分。
2. 按话题建立多个主题。每个主题下收录本部分中与该主题相关的全部实质信息。
3. 不要判断信息是否“重要”；只要包含事实、观点、例子、数字、经历、引用、类比、因果解释、限定条件、反驳、保留意见、分歧或开放问题，就应被保留。
4. 可以删除口癖、寒暄、空泛重复和完全重复表达；不得删除具体细节。
5. 可以合并完全重复的信息；不得合并只是主题相近但细节、角度、例子、限定条件或说话人立场不同的信息。
6. 每个主题内部尽量保留该主题相关讨论的推进顺序：提出、解释、举例、补充、反驳、修正、保留意见。
7. 尽量保留说话人归属、时间戳、人名、机构名、产品名、数字和限定条件。
8. 如果某条实质信息难以归入已有主题，请新建主题，不要省略。
9. 如果上文片段仅用于衔接参考，不要重复输出它。

输出格式：
- 使用 Markdown。
- 每个主题用 `## 主题：...`。
- 每个主题下使用 `【相关发言与信息】`。
- 如该主题包含分歧、保留意见或开放问题，在同一主题下添加 `【分歧、保留与开放问题】`。
- 不要添加总摘要、前言或结语。

第一步材料片段：
{notes}"""

_STEP2_FINAL_PROMPT = """\
下面是基于“第一步：按发言顺序整理”材料分段生成的“第二步：主题化重组笔记”片段。请把它们合并成一份完整的“第二步：主题化重组笔记”。

要求：
1. 不要生成摘要；只做跨片段的主题合并和去重。
2. 按话题合并同类主题，但不要丢失任何具体信息。
3. 不要把不同说话人的不同立场压平为单一结论。
4. 可以去除完全重复的信息；不得因为“看起来相近”就删除细节。
5. 不得合并只是主题相近但细节、角度、例子、限定条件或说话人立场不同的信息。
6. 每个主题内部尽量保留相关讨论的推进顺序：提出、解释、举例、补充、反驳、修正、保留意见。
7. 尽量保留说话人归属、时间戳、人名、机构名、产品名、数字和限定条件。
8. 如果某条实质信息难以归入已有主题，请新建主题，不要省略。
9. 用简体中文输出，自然可读，但不要为了简洁牺牲信息完整性。

输出格式：
- 使用 Markdown。
- 每个主题用 `## 主题：...`。
- 每个主题下使用 `【相关发言与信息】`。
- 如该主题包含分歧、保留意见或开放问题，在同一主题下添加 `【分歧、保留与开放问题】`。
- 不要添加总摘要、前言或结语。

分段笔记：
{summaries}"""

# qwen-mt-lite has a smaller token budget than qwen-plus, but transcript
# translation has no instruction prompt. 10000 chars keeps requests reasonably
# large while leaving output headroom for dense Chinese or longer translations.
_MT_CHUNK_CHARS = 10_000
_MT_MAX_TOKENS = 8192

# Formalization pass (qwen-plus). Chinese: ~1.5 chars/token.
# 15 000 chars ≈ 10 000 input tokens; output is similar length (no summarization),
# fitting comfortably within qwen-plus's 8 192-token output budget.
_FORMALIZE_CHUNK_CHARS = 15_000

_FORMALIZE_SYSTEM = (
    "You are a senior Chinese transcript editor. "
    "Your task is to produce an edited transcript that stays close to the original wording and speaking order, "
    "while removing oral redundancy and text that carries no useful information. "
    "Preserve every substantive claim, fact, number, example, quotation, caveat, condition, contrast, and speaker/time marker. "
    "Do not summarize, reorganize by topic, add conclusions, or turn the transcript into notes. "
    "You may delete or shorten non-substantive oral clutter when doing so does not change meaning, nuance, emphasis, or uncertainty. "
    "Use sensible paragraph breaks to make the transcript readable without adding headings or changing the structure."
)

_FORMALIZE_SINGLE_PROMPT = """\
The following text is a Chinese transcript or machine-translated Chinese transcript. It may be oral, repetitive, and poorly paragraphed.

Edit it into a professional readable transcript, not a summary. The target style is: as close to a transcript as possible, but cleaned by a careful editor.

Rules, in priority order:

1. Preserve all substantive information: claims, facts, data, examples, quotations, reasons, comparisons, caveats, conditions, uncertainty, disagreement, emphasis, and speaker/time markers.
2. Preserve the original speaking order and local flow. Do not reorganize by topic, add headings, add conclusions, or turn the content into notes.
3. Remove or shorten non-substantive oral clutter, including:
   - filler words and hesitation markers: "嗯", "啊", "呃", "就是", "然后就是", "怎么说", "you know", "I mean", "sort of", "kind of", when they do not carry meaning;
   - repeated starts, false starts, and self-corrections that do not add information;
   - backchannel acknowledgements and empty confirmations, such as "对对对", "是的是的", "right", when they only keep the conversation going;
   - duplicated phrases or sentences that express the exact same meaning.
4. Keep wording close to the original. Prefer deletion of useless words and light sentence repair over rewriting in a new style.
5. Do not delete material that carries attitude, uncertainty, limitation, contrast, emphasis, speaker stance, or rhetorical intent.
6. Merge only exact or near-exact repetitions. Do not merge passages that contain different details, examples, conditions, angles, or speaker positions.
7. Improve punctuation, paragraphing, and sentence boundaries for readability. Start a new paragraph when speaker labels or timestamps change. Also split long speeches at natural shifts such as a new point, example, clarification, contrast, objection, answer, or transition, but do not add headings or bullet lists.
8. Keep the output in Simplified Chinese. Preserve English names, product names, company names, technical terms, and numbers when present.

Output only the edited transcript. Do not add explanations, a preface, headings, or a summary.

---

{text}"""

_FORMALIZE_CHUNK_PROMPT = """\
This is part {part} of {total} of a Chinese transcript or machine-translated Chinese transcript.{context_block}

Edit only this part into a professional readable transcript, not a summary. The target style is: as close to a transcript as possible, but cleaned by a careful editor.

Rules, in priority order:

1. Preserve all substantive information in this part: claims, facts, data, examples, quotations, reasons, comparisons, caveats, conditions, uncertainty, disagreement, emphasis, and speaker/time markers.
2. Preserve this part's original speaking order and local flow. Do not reorganize by topic, add headings, add conclusions, or turn the content into notes.
3. Remove or shorten non-substantive oral clutter, including:
   - filler words and hesitation markers: "嗯", "啊", "呃", "就是", "然后就是", "怎么说", "you know", "I mean", "sort of", "kind of", when they do not carry meaning;
   - repeated starts, false starts, and self-corrections that do not add information;
   - backchannel acknowledgements and empty confirmations, such as "对对对", "是的是的", "right", when they only keep the conversation going;
   - duplicated phrases or sentences that express the exact same meaning.
4. Keep wording close to the original. Prefer deletion of useless words and light sentence repair over rewriting in a new style.
5. Do not delete material that carries attitude, uncertainty, limitation, contrast, emphasis, speaker stance, or rhetorical intent.
6. Merge only exact or near-exact repetitions. Do not merge passages that contain different details, examples, conditions, angles, or speaker positions.
7. Improve punctuation, paragraphing, and sentence boundaries for readability. Start a new paragraph when speaker labels or timestamps change. Also split long speeches at natural shifts such as a new point, example, clarification, contrast, objection, answer, or transition, but do not add headings or bullet lists.
8. Keep the output in Simplified Chinese. Preserve English names, product names, company names, technical terms, and numbers when present.
9. The preceding context is only for continuity. Do not repeat it.

Output only the edited content for this part. Do not add explanations, a preface, headings, or a summary.

---

{text}"""


def _qwen_chat(prompt: str, max_tokens: int | None = None, model: str | None = None,
               system: str | None = None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    kwargs: dict = dict(
        model=model or QWEN_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": system or _SUMMARY_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
    )
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into N evenly-sized chunks where N = ceil(len(text) / max_chars).

    Computing N upfront and distributing evenly avoids the lopsided-remainder
    problem of the naive fill-to-max approach (e.g. 81k chars with an 80k limit
    would produce 80k + 1k; this instead yields 2 × 40.9k).

    Each chunk targets len(remaining) / slots_left chars and breaks at the
    nearest sentence or paragraph boundary within a ±25% window of that target.
    """
    import math
    if len(text) <= max_chars:
        return [text]

    n = math.ceil(len(text) / max_chars)
    chunks = []
    remaining = text

    for i in range(n - 1):
        slots_left = n - i
        target = len(remaining) // slots_left          # recomputed each iteration
        lo = max(target // 2, 1)
        hi = min(target + target // 4, len(remaining) - 1)

        cut = -1
        for sep in ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; "):
            pos = remaining.rfind(sep, lo, hi)
            if pos != -1:
                cut = pos + len(sep)
                break
        if cut < 0:
            cut = target                               # hard cut if no boundary found

        chunks.append(remaining[:cut])
        remaining = remaining[cut:]

    chunks.append(remaining)
    return chunks


def _translate_chunk(text: str, prev_translated_tail: str = "") -> str:
    """Translate one chunk via qwen-mt-lite.

    qwen-mt-lite only accepts 'user' and 'assistant' roles — 'system' is not supported
    and causes a 400 error. With translation_options active the model translates the
    user message literally, so instructions or context must not be injected there either.
    The prev_translated_tail arg is kept for API compatibility but is intentionally unused.
    """
    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    resp = client.chat.completions.create(
        model=QWEN_TRANSLATION_MODEL,
        messages=[
            {"role": "user", "content": text},
        ],
        max_tokens=_MT_MAX_TOKENS,
        extra_body={"translation_options": {"source_lang": "auto", "target_lang": "Chinese"}},
    )
    return resp.choices[0].message.content.strip()


def _translate_to_chinese(text: str) -> str:
    """Translate transcript text to Simplified Chinese using qwen-mt-lite."""
    chunks = _split_into_chunks(text, _MT_CHUNK_CHARS)
    if len(chunks) == 1:
        logger.info("Translating single chunk (%d chars)", len(text))
        return _translate_chunk(text)
    logger.info("Translating in %d chunks (total %d chars)", len(chunks), len(text))
    results = []
    prev_tail = ""
    for i, chunk in enumerate(chunks):
        translated = _translate_chunk(chunk, prev_tail)
        results.append(translated)
        prev_tail = translated[-200:] if len(translated) > 200 else translated
        logger.info("Translated chunk %d/%d", i + 1, len(chunks))
    return "".join(results)


def _formalize_chinese(text: str) -> str:
    """Pass 2: clean up translated Chinese — remove fillers/repetitions, add paragraphing."""
    chunks = _split_into_chunks(text, _FORMALIZE_CHUNK_CHARS)
    if len(chunks) == 1:
        logger.info("Formalizing single chunk (%d chars)", len(text))
        return _qwen_chat(
            _FORMALIZE_SINGLE_PROMPT.format(text=text),
            system=_FORMALIZE_SYSTEM,
        )

    total = len(chunks)
    logger.info("Formalizing in %d chunks (%d chars)", total, len(text))
    results: list[str] = []
    for i, chunk in enumerate(chunks):
        if results:
            tail = results[-1][-400:]
            context_block = f"\n\n【上文结尾（供衔接参考，请勿重复输出）】\n{tail}"
        else:
            context_block = ""
        result = _qwen_chat(
            _FORMALIZE_CHUNK_PROMPT.format(
                part=i + 1, total=total,
                context_block=context_block,
                text=chunk,
            ),
            system=_FORMALIZE_SYSTEM,
        )
        results.append(result)
        logger.info("Formalized chunk %d/%d", i + 1, total)
    return "\n\n".join(results)


def _detect_language(text: str) -> str:
    """Return 'zh' if the text is predominantly Chinese, else 'en'.
    Samples the first 5 000 chars for speed; treats >20% CJK chars as Chinese."""
    sample = text[:5000]
    cjk = sum(1 for c in sample if '一' <= c <= '鿿')
    non_space = sum(1 for c in sample if not c.isspace())
    return 'zh' if non_space and cjk / non_space > 0.20 else 'en'


def _summarize_transcript(transcript: str) -> str:
    lang = _detect_language(transcript)
    step1_max_chars = _MAX_CHARS_ZH if lang == 'zh' else _MAX_CHARS_EN
    logger.info(
        "Summarizing transcript with shared pipeline: lang=%s, %d chars, step1_chunk_size=%d",
        lang, len(transcript), step1_max_chars,
    )

    # ---- Step 1: order-preserving rewrite ----
    step1_chunks = _split_into_chunks(transcript, step1_max_chars)
    if len(step1_chunks) == 1:
        step1_result = _qwen_chat(
            _STEP1_SINGLE_PROMPT.format(transcript=transcript),
            system=_SUMMARY_SYSTEM,
        )
    else:
        total = len(step1_chunks)
        logger.info("Step 1 requires %d chunks", total)
        step1_parts: list[str] = []
        for i, chunk in enumerate(step1_chunks):
            if i > 0:
                tail = step1_chunks[i - 1][-_CHUNK_OVERLAP:]
                context_block = f"\n\n[上一个原文片段，仅供衔接参考，请勿重复输出]\n{tail}"
            else:
                context_block = ""
            part_result = _qwen_chat(
                _STEP1_CHUNK_PROMPT.format(
                    part=i + 1,
                    total=total,
                    context_block=context_block,
                    transcript=chunk,
                ),
                system=_SUMMARY_SYSTEM,
            )
            step1_parts.append(part_result)
            logger.info("Step 1 chunk %d/%d complete", i + 1, total)
        step1_result = "\n\n".join(step1_parts)

    # ---- Step 2: structured summary from step 1 ----
    step2_max_chars = _MAX_CHARS_ZH
    step2_chunks = _split_into_chunks(step1_result, step2_max_chars)
    if len(step2_chunks) == 1:
        step2_result = _qwen_chat(
            _STEP2_SINGLE_PROMPT.format(notes=step1_result),
            system=_SUMMARY_SYSTEM,
        )
    else:
        total = len(step2_chunks)
        logger.info("Step 2 requires %d chunks", total)
        step2_parts: list[str] = []
        for i, chunk in enumerate(step2_chunks):
            if i > 0:
                tail = step2_chunks[i - 1][-_CHUNK_OVERLAP:]
                context_block = f"\n\n[上一个第一步片段，仅供衔接参考，请勿重复输出]\n{tail}"
            else:
                context_block = ""
            part_result = _qwen_chat(
                _STEP2_CHUNK_PROMPT.format(
                    part=i + 1,
                    total=total,
                    context_block=context_block,
                    notes=chunk,
                ),
                system=_SUMMARY_SYSTEM,
            )
            step2_parts.append(f"Section {i + 1}:\n{part_result}")
            logger.info("Step 2 chunk %d/%d complete", i + 1, total)
        step2_result = _qwen_chat(
            _STEP2_FINAL_PROMPT.format(summaries="\n\n".join(step2_parts)),
            system=_SUMMARY_SYSTEM,
        )

    final_output = (
        "## Step 2 Topic-Reorganized Notes\n\n"
        f"{step2_result.strip()}\n\n"
        "---\n\n"
        "## Step 1 Ordered Rewrite\n\n"
        f"{step1_result.strip()}"
    )
    logger.info(
        "Summary pipeline complete: step1_chars=%d step2_chars=%d final_chars=%d",
        len(step1_result), len(step2_result), len(final_output),
    )
    return final_output



# ---------------------------------------------------------------------------
# Entry points — each called from app.py in a daemon thread
# ---------------------------------------------------------------------------

def process_transcript_job(job_id: str, video_url: str, video_id: str,
                            mode: str = "no_diarization") -> None:
    """
    First stage.
    YouTube:
      - diarization:    always download audio immediately
      - no_diarization: try captions first; if none, pause at awaiting_approval
    Xiaoyuzhou:
      - diarization:    transcribe direct audio URL immediately
      - no_diarization: pause at awaiting_approval (audio_path stores the URL)
    """
    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Transcript job %s started for %s (mode=%s)", job_id, video_id, mode)

        if is_bilibili_url(video_url):
            # --- Bilibili video — same yt-dlp path as YouTube, using full URL ---
            title, author = _fetch_video_metadata(video_url)
            if title or author:
                db.set_transcript_metadata(job_id, video_title=title, video_author=author)

            if mode == "diarization":
                _run_audio_transcript(job_id, video_url, diarize=True)
            else:
                transcript = _fetch_transcript_fast(video_url)
                if transcript is not None:
                    db.update_transcript_job(job_id, status="done", transcript=transcript)
                    logger.info("Bilibili job %s completed via captions (%d chars)", job_id, len(transcript))
                else:
                    db.update_transcript_job(job_id, status="awaiting_approval")

        elif is_xiaoyuzhou_url(video_url):
            # --- Xiaoyuzhou episode ---
            meta = _fetch_xiaoyuzhou_metadata(video_id)
            title  = meta.get("title")
            author = meta.get("author")
            if title or author:
                db.set_transcript_metadata(job_id, video_title=title, video_author=author)
                logger.info("Xiaoyuzhou metadata for %s: title=%r author=%r", video_id, title, author)

            audio_url = meta.get("audio_url")
            if not audio_url:
                raise RuntimeError("Could not find audio URL for this Xiaoyuzhou episode")

            if mode == "diarization":
                _run_url_audio_transcript(job_id, audio_url, diarize=True)
            else:
                # Store audio URL in audio_path so continue_audio_transcript can find it
                db.update_transcript_job(job_id, status="awaiting_approval", audio_path=audio_url)
                logger.info("Xiaoyuzhou job %s: awaiting user approval for audio transcription", job_id)

        else:
            # --- YouTube video ---
            title, author = _fetch_video_metadata(video_id)
            if title or author:
                db.set_transcript_metadata(job_id, video_title=title, video_author=author)
                logger.info("Metadata for %s: title=%r author=%r", video_id, title, author)

            if mode == "diarization":
                _run_audio_transcript(job_id, video_id, diarize=True)
            else:
                transcript = _fetch_transcript_fast(video_id)
                if transcript is not None:
                    db.update_transcript_job(job_id, status="done", transcript=transcript)
                    logger.info(
                        "Transcript job %s completed via captions (%d chars)", job_id, len(transcript)
                    )
                else:
                    logger.info(
                        "No captions for %s — awaiting user approval for audio fallback", video_id
                    )
                    db.update_transcript_job(job_id, status="awaiting_approval")

    except Exception as exc:
        logger.exception("Transcript job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))


def process_uploaded_transcript_job(job_id: str, media_path: str, original_filename: str, mode: str) -> None:
    """Process a user-uploaded audio or video file through the ASR pipeline."""
    try:
        title = os.path.splitext(os.path.basename(original_filename))[0] or original_filename
        db.set_transcript_metadata(job_id, video_title=title, video_author=None)
        _run_local_media_transcript(job_id, media_path, diarize=(mode == "diarization"))
    except Exception as exc:
        logger.exception("Uploaded transcript job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc), audio_path=media_path)


def continue_audio_transcript(job_id: str, video_id: str) -> None:
    """
    Second stage for no_diarization mode — called after user approves audio fallback.

    Xiaoyuzhou: audio_path contains the direct xyzcdn.net URL → use _run_url_audio_transcript.
    YouTube:    audio_path is a local file path (or None) → use _run_audio_transcript.
    """
    try:
        db.update_transcript_job(job_id, status="processing")
        job = db.get_transcript_job(job_id)
        audio_path = (job or {}).get("audio_path") or ""

        if audio_path.startswith("http") and "xyzcdn.net" in audio_path:
            # Xiaoyuzhou — direct public audio URL stored in audio_path
            logger.info("Xiaoyuzhou audio transcription started for job %s", job_id)
            _run_url_audio_transcript(job_id, audio_path, diarize=False)
        else:
            # YouTube / Bilibili — download via yt-dlp
            # Use full video_url from DB so Bilibili gets the correct URL format
            dl_target = (job or {}).get("video_url") or video_id
            if is_bilibili_url(dl_target):
                logger.info("Bilibili audio fallback started for job %s", job_id)
            else:
                logger.info("Audio fallback started for job %s, video %s", job_id, video_id)
            _run_audio_transcript(job_id, dl_target, diarize=False)
    except Exception as exc:
        logger.exception("Audio fallback job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))


def retry_audio_transcript(job_id: str) -> None:
    """
    Retry transcription for a failed job, reusing the cached audio file (no re-download).
    Called when status is 'error' and audio_path is set in the DB.
    """
    try:
        job = db.get_transcript_job(job_id)
        if not job:
            logger.error("retry_audio_transcript: job %s not found", job_id)
            return
        audio_path = job["audio_path"]
        if not audio_path or not os.path.isfile(audio_path):
            raise FileNotFoundError(
                f"Cached audio not found at {audio_path!r} — re-submit the URL to re-download"
            )
        diarize = job["mode"] == "diarization"
        db.update_transcript_job(job_id, status="processing")
        logger.info("Retrying transcription for job %s (audio=%s)", job_id, audio_path)
        if (job["input_type"] or "url") == "upload":
            _run_local_media_transcript(job_id, audio_path, diarize=diarize)
        else:
            _run_audio_transcript(job_id, job["video_id"], diarize=diarize, existing_audio_path=audio_path)
    except Exception as exc:
        logger.exception("Retry transcription for job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))


def translate_transcript(job_id: str) -> None:
    """
    User-activated translation. Called from app.py after status is set to 'translating'.
    Translates the original transcript to Simplified Chinese and stores in transcript_zh.
    On failure: reverts to 'done' so user can retry.
    """
    try:
        job = db.get_transcript_job(job_id)
        transcript = job["transcript"] if job else None
        if not transcript:
            logger.error("Job %s has no transcript to translate", job_id)
            db.update_transcript_job(job_id, status="done")
            return

        lang = _detect_language(transcript)
        logger.info(
            "Preparing Chinese transcript for job %s (%d chars, lang=%s)",
            job_id,
            len(transcript),
            lang,
        )
        if lang == "zh":
            transcript_zh = transcript
            logger.info("Source is Chinese; skipping machine translation")
        else:
            transcript_zh = _translate_to_chinese(transcript)
            logger.info("Translation pass done (%d chars); formalizing...", len(transcript_zh))
        transcript_zh = _formalize_chinese(transcript_zh)
        db.update_transcript_job(job_id, status="done", transcript_zh=transcript_zh)
        logger.info("Translation+formalization done for job %s (%d chars)", job_id, len(transcript_zh))

    except Exception as exc:
        logger.exception("Translation for job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="done")  # revert so user can retry


def generate_transcript_summary(job_id: str) -> None:
    """
    User-activated summarization. Sets status 'summarizing' before being called.
    Uses transcript_zh as source if available (Chinese → Chinese summary);
    otherwise summarizes the original transcript and instructs the LLM to output Chinese.
    On failure: reverts to 'done' so user can retry.
    """
    try:
        job = db.get_transcript_job(job_id)
        if not job:
            logger.error("Job %s not found", job_id)
            db.update_transcript_job(job_id, status="done")
            return

        # Prefer the Chinese transcript as input so the summary is purely Chinese→Chinese
        source = job["transcript_zh"] or job["transcript"]
        if not source:
            logger.error("Job %s has no transcript to summarize", job_id)
            db.update_transcript_job(job_id, status="done")
            return

        logger.info(
            "Summarizing transcript for job %s (%d chars, source=%s)",
            job_id, len(source), "zh" if job["transcript_zh"] else "original",
        )
        summary = _summarize_transcript(source)
        db.update_transcript_job(job_id, status="done", summary=summary)
        logger.info("Summary generated for job %s", job_id)

    except Exception as exc:
        logger.exception("Summary generation for job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="done")  # revert so user can retry
