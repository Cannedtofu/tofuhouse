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
from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL, SOCKS_PROXY, YOUTUBE_COOKIES_FILE

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
    Try to download subtitles via yt-dlp (caption file only, no audio).
    Pass 1: English + Chinese; Pass 2: any language.
    Returns plain text or None to trigger audio fallback.
    """
    import yt_dlp

    tmp_dir = tempfile.mkdtemp(prefix="transcript_sub_")
    try:
        base_opts = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "skip_download": True,
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
            "quiet": False,
            "no_warnings": False,
            "socket_timeout": 30,
            "retries": 5,
        }
        if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
            base_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        if SOCKS_PROXY:
            base_opts["proxy"] = SOCKS_PROXY
        else:
            logger.warning("yt-dlp subtitle: no proxy configured — may be blocked on cloud IPs")

        def _collect_files() -> str | None:
            for ext in (".vtt", ".srt"):
                for fname in sorted(os.listdir(tmp_dir)):
                    if fname.endswith(ext):
                        fpath = os.path.join(tmp_dir, fname)
                        with open(fpath, encoding="utf-8") as f:
                            raw = f.read()
                        text = _parse_vtt(raw)
                        if text:
                            logger.info("  using subtitle file %s (%d chars raw)", fname, len(raw))
                            return text
                        logger.warning("  subtitle file %s parsed to empty text", fname)
            return None

        def _try_download(langs: list[str]) -> str | None:
            opts = {**base_opts, "subtitleslangs": langs}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            except Exception as exc:
                logger.warning("yt-dlp subtitle download error (langs=%s) for %s: %s", langs, video_id, exc)
                return None
            return _collect_files()

        preferred = ["en", "en-US", "en-GB", "en-AU", "zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-HK"]
        text = _try_download(preferred)
        if text:
            logger.info("Subtitle fetched via yt-dlp for %s (%d chars)", video_id, len(text))
            return text

        logger.info("No preferred-language subtitles for %s — retrying with all languages", video_id)
        text = _try_download(["all"])
        if text:
            logger.info("Subtitle fetched (any language) for %s (%d chars)", video_id, len(text))
            return text

        logger.info("No subtitle file found for %s — listing tmp dir: %s", video_id, os.listdir(tmp_dir))
        return None

    except Exception as exc:
        logger.warning("yt-dlp subtitle fast path failed for %s: %s", video_id, exc)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
    Transcribe a full audio file with paraformer-v2 via DashScope Recognition.
    paraformer-v2 supports up to 2 GB / 12 hours per call (diarization recommended ≤2 h).
    No chunking needed — the full file is sent in one call, which gives consistent
    speaker IDs throughout when diarization is enabled.
    """
    import dashscope
    from dashscope.audio.asr import Recognition

    dashscope.api_key = QWEN_API_KEY
    file_uri = f"file://{os.path.abspath(audio_path)}"

    audio_fmt = os.path.splitext(audio_path)[1].lstrip(".") or "mp3"
    kwargs: dict = dict(
        model="paraformer-v2",
        file=file_uri,
        format=audio_fmt,
        language_hints=["zh", "en"],
    )
    if diarize:
        kwargs["diarization_enabled"] = True

    response = Recognition().call(**kwargs)

    if response.status_code != 200:
        raise RuntimeError(
            f"paraformer-v2 error {response.status_code}: {response.message}"
        )

    output    = response.output or {}
    sentences = output.get("sentence_info") or []

    if not sentences:
        return output.get("text", "") or ""

    if diarize:
        return _format_diarized_sentences(sentences)

    return " ".join(s.get("text", "") for s in sentences)


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

def _run_audio_transcript(job_id: str, video_id: str, diarize: bool = False) -> None:
    """Download full audio, transcribe in one paraformer-v2 call, store transcript."""
    tmp_dir = tempfile.mkdtemp(prefix="transcript_")
    try:
        audio_path = _download_audio(video_id, tmp_dir)
        logger.info("Audio downloaded to %s", audio_path)

        logger.info("Transcribing full audio with paraformer-v2 (diarize=%s)", diarize)
        transcript = _transcribe_audio_file(audio_path, diarize=diarize)

        db.update_transcript_job(job_id, status="done", transcript=transcript)
        logger.info("Audio transcript job %s done (%d chars)", job_id, len(transcript))

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Summarization — Qwen via DashScope
# ---------------------------------------------------------------------------

_TRANSCRIPT_SUMMARY_SYSTEM = (
    "You are a helpful assistant that summarizes video transcripts clearly and concisely. "
    "Respond in the same language as the majority of the transcript content."
)

_TRANSCRIPT_SUMMARY_PROMPT = """\
Please summarize the following video transcript in three clearly labeled sections:

**Overview** (2-3 sentences): What is this video about?

**Key Topics** (bulleted list): What are the main subjects covered?

**Main Takeaways** (bulleted list): What are the key conclusions or insights?

Transcript:
{transcript}"""

_CHUNK_SUMMARY_PROMPT = """\
Summarize the key points from this section (part {part} of {total}) of a video transcript. \
Be concise and factual.

Transcript section:
{transcript}"""

_FINAL_SUMMARY_PROMPT = """\
Based on these section-by-section summaries from a longer video transcript, \
write a final summary in three clearly labeled sections:

**Overview** (2-3 sentences): What is this video about?

**Key Topics** (bulleted list): What are the main subjects covered?

**Main Takeaways** (bulleted list): What are the key conclusions or insights?

Section summaries:
{summaries}"""


def _qwen_chat(prompt: str, max_tokens: int = 1024) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    resp = client.chat.completions.create(
        model=QWEN_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": _TRANSCRIPT_SUMMARY_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


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


def generate_transcript_summary(job_id: str) -> None:
    """
    User-activated summarization. Sets status 'summarizing' before being called.
    On success: status stays 'done' with summary populated.
    On failure: reverts to 'done' without summary so user can retry.
    """
    try:
        job = db.get_transcript_job(job_id)
        transcript = job["transcript"] if job else None
        if not transcript:
            logger.error("Job %s has no transcript to summarize", job_id)
            db.update_transcript_job(job_id, status="done")
            return

        logger.info("Summarizing transcript for job %s (%d chars)", job_id, len(transcript))
        summary = _summarize_transcript(transcript)
        db.update_transcript_job(job_id, status="done", summary=summary)
        logger.info("Summary generated for job %s", job_id)

    except Exception as exc:
        logger.exception("Summary generation for job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="done")  # revert so user can retry
