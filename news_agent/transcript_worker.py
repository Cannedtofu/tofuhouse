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
  - With diarization:    sentence_info with speaker_id, formatted as [Speaker A] lines;
                         consistent speaker IDs throughout the entire video
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile

import db
import secrets

import audio_registry
from config import (
    APP_BASE_URL,
    AUDIO_CACHE_DIR,
    QWEN_API_KEY,
    QWEN_BASE_URL,
    QWEN_SUMMARY_MODEL,
    QWEN_TRANSLATION_MODEL,
    SOCKS_PROXY,
    YOUTUBE_COOKIES_FILE,
)

logger = logging.getLogger(__name__)

_MAX_CHARS_PER_LLM_CALL = 12000


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str | None:
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


# ---------------------------------------------------------------------------
# Fast path — yt-dlp subtitle download (caption file only, no audio)
# ---------------------------------------------------------------------------

def _parse_vtt(content: str) -> str:
    """Convert WebVTT (including YouTube karaoke-style) to plain text."""
    content = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", content)
    content = re.sub(r"<[^>]+>", "", content)
    texts = []
    prev = None
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if (line.startswith("WEBVTT") or "-->" in line
                or re.match(r"^\d+$", line)
                or re.match(r"^[A-Z][a-z]+: ", line)):
            continue
        if line != prev:
            texts.append(line)
            prev = line
    return " ".join(texts)


def _fetch_transcript_fast(video_id: str) -> str | None:
    """
    Probe available subtitles via yt-dlp extract_info, pick the best VTT URL,
    download it through yt-dlp's own networking (proxy-aware), then parse.
    Returns plain text or None to trigger audio fallback.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 5,
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        ydl_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        ydl_opts["proxy"] = SOCKS_PROXY
    else:
        logger.warning("yt-dlp subtitle: no proxy configured — may be blocked on cloud IPs")

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Step 1: probe what subtitle tracks actually exist
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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

def _download_audio(video_id: str, tmp_dir: str) -> str:
    import yt_dlp

    output_template = os.path.join(tmp_dir, f"{video_id}.%(ext)s")
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

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    for fname in os.listdir(tmp_dir):
        if fname.startswith(video_id):
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
    """Group consecutive sentences by speaker_id and format as labeled paragraphs."""
    lines: list[str] = []
    current_speaker = None
    current_parts: list[str] = []

    for s in sentences:
        spk  = s.get("speaker_id")
        text = s.get("text", "").strip()
        if not text:
            continue
        if spk != current_speaker:
            if current_parts:
                lines.append(f"[{_speaker_label(current_speaker)}] {' '.join(current_parts)}")
            current_speaker = spk
            current_parts   = [text]
        else:
            current_parts.append(text)

    if current_parts:
        lines.append(f"[{_speaker_label(current_speaker)}] {' '.join(current_parts)}")

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
            "model": "paraformer-v2",
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


# ---------------------------------------------------------------------------
# Video metadata
# ---------------------------------------------------------------------------

def _fetch_video_metadata(video_id: str) -> tuple[str | None, str | None]:
    """Return (title, uploader) for a YouTube video. Non-fatal — returns (None, None) on failure."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        opts["cookiefile"] = YOUTUBE_COOKIES_FILE
    if SOCKS_PROXY:
        opts["proxy"] = SOCKS_PROXY

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
            title  = info.get("title")
            author = info.get("uploader") or info.get("channel")
            return title, author
    except Exception as exc:
        logger.warning("Could not fetch metadata for %s: %s", video_id, exc)
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

_TRANSCRIPT_SUMMARY_SYSTEM = (
    "You are a helpful assistant that summarizes video transcripts. "
    "Always write your summary in Simplified Chinese (简体中文), "
    "regardless of the language of the input transcript."
)

_TRANSCRIPT_SUMMARY_PROMPT = """\
Summarize the following video transcript in Simplified Chinese using three clearly labeled sections:

**概述** (2-3 sentences): What is this video about?

**主要话题** (bulleted list): What are the main subjects covered?

**核心要点** (bulleted list): What are the key conclusions or insights?

Transcript:
{transcript}"""

_CHUNK_SUMMARY_PROMPT = """\
Summarize the key points from this section (part {part} of {total}) of a video transcript \
in Simplified Chinese. Be concise and factual.

Transcript section:
{transcript}"""

_FINAL_SUMMARY_PROMPT = """\
Based on these section summaries from a longer video transcript, \
write a final summary in Simplified Chinese using three clearly labeled sections:

**概述** (2-3 sentences): What is this video about?

**主要话题** (bulleted list): What are the main subjects covered?

**核心要点** (bulleted list): What are the key conclusions or insights?

Section summaries:
{summaries}"""

# qwen-mt-lite has a smaller token budget; 4000 chars ≈ 1000 English tokens,
# leaving headroom for system message and output tokens.
_MT_CHUNK_CHARS = 4000


def _qwen_chat(prompt: str, max_tokens: int = 1024, model: str | None = None,
               system: str | None = None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    resp = client.chat.completions.create(
        model=model or QWEN_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": system or _TRANSCRIPT_SUMMARY_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks ≤ max_chars, breaking at sentence/paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        cut = -1
        for sep in ("\n\n", "\n", ". ", "! ", "? "):
            pos = remaining.rfind(sep, max_chars // 2, max_chars)
            if pos != -1:
                cut = pos + len(sep)
                break
        if cut < 0:
            cut = max_chars
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _translate_chunk(text: str, prev_translated_tail: str = "") -> str:
    """Translate one chunk via qwen-mt-lite with optional preceding-context for continuity."""
    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    system = (
        "You are a professional translator. Translate the user's text into Simplified Chinese. "
        "Output only the translated text. Preserve speaker labels like [Speaker A] unchanged."
    )
    if prev_translated_tail:
        system += (
            "\n\nFor continuity, the immediately preceding passage was already translated as:\n"
            + prev_translated_tail
        )
    resp = client.chat.completions.create(
        model=QWEN_TRANSLATION_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": text},
        ],
        max_tokens=4096,
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


def _summarize_transcript(transcript: str) -> str:
    if len(transcript) <= _MAX_CHARS_PER_LLM_CALL:
        return _qwen_chat(
            _TRANSCRIPT_SUMMARY_PROMPT.format(transcript=transcript),
            max_tokens=1024,
        )

    raw_chunks = [
        transcript[i : i + _MAX_CHARS_PER_LLM_CALL]
        for i in range(0, len(transcript), _MAX_CHARS_PER_LLM_CALL)
    ]
    total = len(raw_chunks)
    logger.info("Transcript too long (%d chars); summarizing in %d chunks", len(transcript), total)

    chunk_summaries = []
    for i, chunk in enumerate(raw_chunks):
        summary = _qwen_chat(
            _CHUNK_SUMMARY_PROMPT.format(part=i + 1, total=total, transcript=chunk),
            max_tokens=512,
        )
        chunk_summaries.append(f"Part {i + 1}:\n{summary}")

    return _qwen_chat(
        _FINAL_SUMMARY_PROMPT.format(summaries="\n\n".join(chunk_summaries)),
        max_tokens=1024,
    )


# ---------------------------------------------------------------------------
# Entry points — each called from app.py in a daemon thread
# ---------------------------------------------------------------------------

def process_transcript_job(job_id: str, video_url: str, video_id: str,
                            mode: str = "no_diarization") -> None:
    """
    First stage.
    - diarization:    always start audio download immediately (user chose this mode knowingly)
    - no_diarization: try captions first; if none, pause at awaiting_approval
    """
    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Transcript job %s started for %s (mode=%s)", job_id, video_id, mode)

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


def continue_audio_transcript(job_id: str, video_id: str) -> None:
    """
    Second stage for no_diarization mode — called after user approves audio fallback.
    Uses paraformer-v2 without diarization.
    """
    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Audio fallback started for job %s, video %s", job_id, video_id)
        _run_audio_transcript(job_id, video_id, diarize=False)
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

        logger.info("Translating transcript for job %s (%d chars)", job_id, len(transcript))
        transcript_zh = _translate_to_chinese(transcript)
        db.update_transcript_job(job_id, status="done", transcript_zh=transcript_zh)
        logger.info("Translation done for job %s (%d chars)", job_id, len(transcript_zh))

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
