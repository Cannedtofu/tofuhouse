"""
Background worker for YouTube transcript extraction and summarization.

Workflow:
  process_transcript_job()  — called immediately after POST /transcript/process
    1. Set job status → "processing"
    2. Fast path: yt-dlp subtitle download (caption file only, no audio)
    3a. Captions found → set status "done" with transcript (no summary yet)
    3b. No captions → set status "awaiting_approval" and stop; user must confirm

  continue_audio_transcript()  — called after user approves audio fallback
    1. Set status → "processing"
    2. yt-dlp: download audio-only (mp3, mono, 64 kbps)
    3. pydub: split on silence at ~4-min boundaries
    4. qwen3-asr-flash: transcribe all chunks in parallel
    5. Set status "done" with transcript (no summary yet)

  generate_transcript_summary()  — called after user clicks "Generate AI Summary"
    1. Set status → "summarizing"
    2. Summarize transcript with Qwen qwen-plus
    3. Set status "done" with summary added

All temp files are cleaned up in try/finally blocks even on error.
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

# Target audio chunk size in milliseconds — must stay under qwen3-asr-flash's 5-min/10MB limit
_TARGET_CHUNK_MS = 4 * 60 * 1000
# How far from the target boundary to search for a silence midpoint (30 s)
_SILENCE_SEEK_MS = 30 * 1000
# Overlap between adjacent chunks to avoid losing words at the cut boundary (~1.5 s)
_CHUNK_OVERLAP_MS = 1500
# Maximum transcript characters sent to Qwen per LLM call before chunked summarization kicks in
_MAX_CHARS_PER_LLM_CALL = 12000


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str | None:
    """Return the 11-character video ID from any recognized YouTube URL, or None."""
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
    """Return True if the URL is a recognizable YouTube video URL."""
    return extract_video_id(url) is not None


# ---------------------------------------------------------------------------
# Step 4a: Fast path — yt-dlp subtitle download (caption file only, no audio)
# ---------------------------------------------------------------------------

def _parse_vtt(content: str) -> str:
    """Convert WebVTT (including YouTube karaoke-style) to plain text."""
    # Strip inline timestamp marks like <00:00:01.234>
    content = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", content)
    # Strip HTML tags like <c>, </c>, <b>
    content = re.sub(r"<[^>]+>", "", content)

    texts = []
    prev = None
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip WEBVTT header, timestamp lines (-->), numeric cue IDs, metadata
        if (line.startswith("WEBVTT") or "-->" in line
                or re.match(r"^\d+$", line)
                or re.match(r"^[A-Z][a-z]+: ", line)):
            continue
        if line != prev:  # rolling dedup removes karaoke duplicates
            texts.append(line)
            prev = line
    return " ".join(texts)


def _fetch_transcript_fast(video_id: str) -> str | None:
    """
    Download subtitles via yt-dlp (caption file only, no audio).
    Tries preferred languages first (English + Chinese); if none found,
    retries with all available languages as a catch-all.
    Returns plain text, or None to trigger the audio fallback.
    """
    import yt_dlp  # noqa: PLC0415

    tmp_dir = tempfile.mkdtemp(prefix="transcript_sub_")
    try:
        base_opts = {
            "writesubtitles": True,
            "writeautomaticsub": True,
            "skip_download": True,
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
            base_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        if SOCKS_PROXY:
            base_opts["proxy"] = SOCKS_PROXY
        else:
            logger.warning(
                "yt-dlp subtitle: no proxy configured — may be blocked by YouTube on cloud IPs. "
                "Set SOCKS_PROXY in .env."
            )

        def _try_download(langs: list[str]) -> str | None:
            opts = {**base_opts, "subtitleslangs": langs}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            for ext in (".vtt", ".srt"):
                for fname in sorted(os.listdir(tmp_dir)):
                    if fname.endswith(ext):
                        with open(os.path.join(tmp_dir, fname), encoding="utf-8") as f:
                            raw = f.read()
                        text = _parse_vtt(raw)
                        if text:
                            return text
            return None

        # Pass 1: preferred languages (English + Chinese variants)
        preferred = ["en", "en-US", "en-GB", "en-AU", "zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-HK"]
        text = _try_download(preferred)
        if text:
            logger.info("Subtitle fetched via yt-dlp for %s (%d chars)", video_id, len(text))
            return text

        # Pass 2: accept any available language
        logger.info("No preferred-language subtitles for %s — retrying with all languages", video_id)
        text = _try_download(["all"])
        if text:
            logger.info("Subtitle fetched (any language) via yt-dlp for %s (%d chars)", video_id, len(text))
            return text

        logger.info("No subtitle file found for %s — will try audio fallback", video_id)
        return None

    except Exception as exc:
        logger.info("yt-dlp subtitle download failed for %s: %s", video_id, exc)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Step 4b: Audio download — yt-dlp
# ---------------------------------------------------------------------------

def _download_audio(video_id: str, tmp_dir: str) -> str:
    """
    Download audio-only from YouTube using yt-dlp.
    Saves as mp3, mono, 64 kbps to keep the file small.
    Returns the path to the downloaded audio file.

    Requires a cookies file when YouTube blocks server-side requests.
    Set YOUTUBE_COOKIES_FILE in .env to the path of a Netscape-format cookies file.
    """
    import yt_dlp  # noqa: PLC0415

    output_template = os.path.join(tmp_dir, f"{video_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        # Convert to mp3 at 64 kbps mono via ffmpeg post-processor
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
        # Pass mono flag through postprocessor args
        "postprocessor_args": {"ffmpeg": ["-ac", "1"]},
        "quiet": True,
        "no_warnings": True,
    }

    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        ydl_opts["cookiefile"] = YOUTUBE_COOKIES_FILE
        logger.info("yt-dlp: using cookies file %s", YOUTUBE_COOKIES_FILE)
    if SOCKS_PROXY:
        ydl_opts["proxy"] = SOCKS_PROXY
        logger.info("yt-dlp: routing through proxy %s", SOCKS_PROXY)
    else:
        logger.warning(
            "yt-dlp: no proxy configured — YouTube may block this request on cloud IPs. "
            "Set SOCKS_PROXY in .env."
        )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    # Find the resulting file (extension may vary if ffmpeg isn't available)
    for fname in os.listdir(tmp_dir):
        if fname.startswith(video_id):
            return os.path.join(tmp_dir, fname)

    raise FileNotFoundError(f"yt-dlp produced no output file in {tmp_dir}")


# ---------------------------------------------------------------------------
# Step 4c: Smart audio chunking — pydub
# ---------------------------------------------------------------------------

def _chunk_audio(audio_path: str) -> list[str]:
    """
    Split audio into ~10-minute chunks, always cutting on a silence boundary.

    Strategy:
      - Detect all non-silent ranges in the audio.
      - For each target boundary (every _TARGET_CHUNK_MS), find the nearest
        silence midpoint within ±_SILENCE_SEEK_MS.
      - Each successive chunk begins _CHUNK_OVERLAP_MS before the cut point
        so words at the boundary aren't lost.
      - If the audio is short enough to fit in one chunk, return [audio_path]
        unchanged (no new files created).

    Returns a list of file paths for each chunk.
    """
    from pydub import AudioSegment  # noqa: PLC0415
    from pydub.silence import detect_nonsilent  # noqa: PLC0415

    audio = AudioSegment.from_file(audio_path)
    total_ms = len(audio)

    # Short enough to fit in a single chunk — no splitting needed
    if total_ms <= _TARGET_CHUNK_MS + _SILENCE_SEEK_MS:
        return [audio_path]

    # Detect non-silent sections; silence gaps sit between consecutive ranges
    nonsilent_ranges = detect_nonsilent(audio, min_silence_len=500, silence_thresh=-40)

    # Build list of silence midpoints (center of each gap between spoken regions)
    silence_midpoints: list[int] = []
    for i in range(len(nonsilent_ranges) - 1):
        gap_start = nonsilent_ranges[i][1]
        gap_end = nonsilent_ranges[i + 1][0]
        silence_midpoints.append((gap_start + gap_end) // 2)

    tmp_dir = os.path.dirname(audio_path)
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    chunk_paths: list[str] = []

    start_ms = 0
    chunk_idx = 0

    while start_ms < total_ms:
        target_end = start_ms + _TARGET_CHUNK_MS

        if target_end >= total_ms:
            # Last (possibly shorter) chunk — take everything remaining
            chunk = audio[start_ms:]
            chunk_path = os.path.join(tmp_dir, f"{base_name}_chunk{chunk_idx}.mp3")
            chunk.export(chunk_path, format="mp3")
            chunk_paths.append(chunk_path)
            break

        # Find the silence midpoint nearest to the target boundary (within ±30 s)
        best_split = target_end
        best_dist = float("inf")
        for mid in silence_midpoints:
            dist = abs(mid - target_end)
            if dist <= _SILENCE_SEEK_MS and dist < best_dist:
                best_dist = dist
                best_split = mid

        # Export this chunk
        chunk = audio[start_ms:best_split]
        chunk_path = os.path.join(tmp_dir, f"{base_name}_chunk{chunk_idx}.mp3")
        chunk.export(chunk_path, format="mp3")
        chunk_paths.append(chunk_path)

        # Next chunk starts with a brief overlap so no words are lost at the cut
        start_ms = max(start_ms, best_split - _CHUNK_OVERLAP_MS)
        chunk_idx += 1

    return chunk_paths


# ---------------------------------------------------------------------------
# Step 4d: Speech-to-text — qwen3-asr-flash via DashScope SDK
# ---------------------------------------------------------------------------

def _transcribe_audio_chunk(audio_path: str) -> str:
    """
    Transcribe one audio chunk with qwen3-asr-flash using the DashScope SDK.
    Uses a local file:// URI so no base64 encoding or upload is needed.
    Safe to call from multiple threads simultaneously.
    """
    import dashscope  # noqa: PLC0415
    from dashscope import MultiModalConversation  # noqa: PLC0415

    dashscope.api_key = QWEN_API_KEY

    file_uri = f"file://{os.path.abspath(audio_path)}"
    response = MultiModalConversation.call(
        model="qwen3-asr-flash",
        messages=[{"role": "user", "content": [{"audio": file_uri}]}],
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"qwen3-asr-flash error {response.status_code}: {response.message}"
        )

    return response.output.choices[0].message.content[0]["text"]


def _transcribe_chunks_parallel(chunk_paths: list[str]) -> list[str]:
    """
    Transcribe all chunks concurrently with ThreadPoolExecutor.
    Results are returned in the original chunk order regardless of completion order.
    Cap at 8 workers to avoid overwhelming the API.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

    results: list[str | None] = [None] * len(chunk_paths)
    max_workers = min(len(chunk_paths), 8)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_transcribe_audio_chunk, path): i
            for i, path in enumerate(chunk_paths)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()  # re-raises any exception from the thread

    return results  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Overlap deduplication — reassemble chunk transcripts
# ---------------------------------------------------------------------------

def _remove_boundary_duplicates(texts: list[str]) -> str:
    """
    Join chunk transcripts, removing words duplicated by the overlap region.

    For each pair of adjacent chunks, compare the tail of the first with the
    head of the second (up to 60 words) and trim the matching prefix from the
    second chunk before appending.
    """
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]

    result_words = texts[0].split()

    for next_text in texts[1:]:
        next_words = next_text.split()
        max_check = min(60, len(result_words), len(next_words))
        overlap_found = False

        for overlap_len in range(max_check, 0, -1):
            if result_words[-overlap_len:] == next_words[:overlap_len]:
                result_words.extend(next_words[overlap_len:])
                overlap_found = True
                break

        if not overlap_found:
            result_words.extend(next_words)

    return " ".join(result_words)


# ---------------------------------------------------------------------------
# Step 5: Summarization — Qwen via DashScope
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
    """Single Qwen chat call with the transcript summarization system prompt."""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
    resp = client.chat.completions.create(
        model=QWEN_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": _TRANSCRIPT_SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _summarize_transcript(transcript: str) -> str:
    """
    Summarize a transcript with Qwen.
    If the transcript exceeds _MAX_CHARS_PER_LLM_CALL, it is split into
    overlapping text chunks, each summarized separately, then a final
    combined summary is generated from those chunk summaries.
    """
    if len(transcript) <= _MAX_CHARS_PER_LLM_CALL:
        return _qwen_chat(
            _TRANSCRIPT_SUMMARY_PROMPT.format(transcript=transcript),
            max_tokens=1024,
        )

    # Split transcript into text chunks for long content
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

    combined = "\n\n".join(chunk_summaries)
    return _qwen_chat(
        _FINAL_SUMMARY_PROMPT.format(summaries=combined),
        max_tokens=1024,
    )


# ---------------------------------------------------------------------------
# Entry points — each called from app.py in a daemon thread
# ---------------------------------------------------------------------------

def process_transcript_job(job_id: str, video_url: str, video_id: str) -> None:
    """
    First stage: attempt caption/subtitle extraction.
    Sets status "done" (transcript only) if captions are found.
    Sets status "awaiting_approval" if no captions — user must confirm audio download.
    """
    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Transcript job %s started for video %s", job_id, video_id)

        transcript = _fetch_transcript_fast(video_id)

        if transcript is None:
            logger.info(
                "No captions found for %s — pausing for user approval before audio download",
                video_id,
            )
            db.update_transcript_job(job_id, status="awaiting_approval")
            return

        db.update_transcript_job(job_id, status="done", transcript=transcript)
        logger.info("Transcript job %s completed via captions (%d chars)", job_id, len(transcript))

    except Exception as exc:
        logger.exception("Transcript job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))


def continue_audio_transcript(job_id: str, video_id: str) -> None:
    """
    Second stage (user-approved): download audio and transcribe via ASR.
    Called only after user confirms the audio fallback in the UI.
    Sets status "done" (transcript only) on success.
    """
    tmp_dir: str | None = None

    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Audio fallback started for job %s, video %s", job_id, video_id)

        tmp_dir = tempfile.mkdtemp(prefix="transcript_")
        try:
            audio_path = _download_audio(video_id, tmp_dir)
            logger.info("Audio downloaded to %s", audio_path)

            chunk_paths = _chunk_audio(audio_path)
            logger.info("Audio split into %d chunk(s)", len(chunk_paths))

            logger.info("Transcribing %d chunk(s) in parallel (qwen3-asr-flash)", len(chunk_paths))
            chunk_transcripts = _transcribe_chunks_parallel(chunk_paths)
            transcript = _remove_boundary_duplicates(chunk_transcripts)

        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                tmp_dir = None

        db.update_transcript_job(job_id, status="done", transcript=transcript)
        logger.info("Audio fallback job %s completed (%d chars)", job_id, len(transcript))

    except Exception as exc:
        logger.exception("Audio fallback job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_transcript_summary(job_id: str) -> None:
    """
    Third stage (user-activated): summarize the stored transcript with Qwen.
    Called only after the user clicks "Generate AI Summary" in the UI.
    Sets status back to "done" with summary populated on success.
    On failure, reverts status to "done" so the user can retry.
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
