"""
Background worker for YouTube transcript extraction and summarization.

Workflow (called after POST /transcript/process returns):
  1. Set job status → "processing"
  2. Fast path: youtube-transcript-api (captions)
  3. If captions unavailable → audio fallback:
       a. yt-dlp: download audio-only (mp3, mono, 64 kbps)
       b. pydub: split on silence boundaries at ~10-min intervals
       c. Alibaba STT: transcribe each chunk (placeholder — see _transcribe_audio_chunk)
       d. Reassemble chunks, deduplicating overlap words at boundaries
  4. Summarize full transcript with Qwen (qwen-plus)
  5. Set job status → "done" (or "error" on failure)

All temp files are cleaned up in a try/finally block even on error.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile

import db
from config import QWEN_API_KEY, QWEN_BASE_URL, QWEN_SUMMARY_MODEL

logger = logging.getLogger(__name__)

# Target audio chunk size in milliseconds (~10 minutes)
_TARGET_CHUNK_MS = 10 * 60 * 1000
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
# Step 4a: Fast path — youtube-transcript-api
# ---------------------------------------------------------------------------

def _fetch_transcript_fast(video_id: str) -> str | None:
    """
    Try to fetch English captions via youtube-transcript-api.
    Returns plain text transcript or None if unavailable/failed.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        entries = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB", "en-AU"]
        )
        return " ".join(e["text"] for e in entries)
    except Exception as exc:
        logger.info("youtube-transcript-api unavailable for %s: %s", video_id, exc)
        return None


# ---------------------------------------------------------------------------
# Step 4b: Audio download — yt-dlp
# ---------------------------------------------------------------------------

def _download_audio(video_id: str, tmp_dir: str) -> str:
    """
    Download audio-only from YouTube using yt-dlp.
    Saves as mp3, mono, 64 kbps to keep the file small.
    Returns the path to the downloaded audio file.
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
# Step 4d: Speech-to-text — Alibaba NLS/ISS (PLACEHOLDER)
# ---------------------------------------------------------------------------

def _transcribe_audio_chunk(audio_path: str) -> str:
    """
    Send one audio chunk to Alibaba speech-to-text and return the transcript text.

    THIS IS A PLACEHOLDER — the actual Alibaba NLS/ISS API call must be filled in.

    To implement:
      1. Add the following to .env:
           ALIBABA_NLS_APP_KEY=<your NLS app key>
           ALIBABA_NLS_TOKEN=<your NLS access token>
           ALIBABA_NLS_ENDPOINT=wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1
      2. Install the Alibaba NLS Python SDK:
           pip install aliyun-python-sdk-nls  (or the websocket-based nls package)
      3. Replace the NotImplementedError below with a real API call, for example:

         import nls
         token = os.getenv("ALIBABA_NLS_TOKEN")
         app_key = os.getenv("ALIBABA_NLS_APP_KEY")
         endpoint = os.getenv("ALIBABA_NLS_ENDPOINT", "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1")

         results = []
         def on_result(text, *args): results.append(text)

         transcriber = nls.NlsSpeechTranscriber(
             url=endpoint, app_key=app_key, token=token,
             on_result_changed=on_result, on_completed=lambda *a: None,
         )
         with open(audio_path, "rb") as f:
             audio_bytes = f.read()
         transcriber.start(audio_format="mp3", sample_rate=16000,
                           enable_punctuation_prediction=True)
         transcriber.send_audio(audio_bytes)
         transcriber.stop()
         return "".join(results)

    Docs: https://www.alibabacloud.com/help/en/nls/
    """
    # TODO: implement Alibaba NLS speech-to-text here
    raise NotImplementedError(
        "Alibaba speech-to-text is not yet implemented. "
        "Fill in _transcribe_audio_chunk() in transcript_worker.py and "
        "set ALIBABA_NLS_APP_KEY / ALIBABA_NLS_TOKEN in .env."
    )


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
# Main entry point — called from app.py in a daemon thread
# ---------------------------------------------------------------------------

def process_transcript_job(job_id: str, video_url: str, video_id: str) -> None:
    """
    Execute the full transcript pipeline for one job.
    Must be called in a background thread — never blocks the request.

    On success: stores transcript + summary, sets status "done".
    On any failure: stores error_message, sets status "error".
    Temp files are always cleaned up in the finally block.
    """
    tmp_dir: str | None = None

    try:
        db.update_transcript_job(job_id, status="processing")
        logger.info("Transcript job %s started for video %s", job_id, video_id)

        # ------------------------------------------------------------------
        # 4a: Fast path — youtube-transcript-api
        # ------------------------------------------------------------------
        transcript = _fetch_transcript_fast(video_id)

        if transcript is None:
            # --------------------------------------------------------------
            # 4b: Audio download
            # --------------------------------------------------------------
            logger.info("Falling back to audio download for video %s", video_id)
            tmp_dir = tempfile.mkdtemp(prefix="transcript_")

            try:
                audio_path = _download_audio(video_id, tmp_dir)
                logger.info("Audio downloaded to %s", audio_path)

                # ----------------------------------------------------------
                # 4c: Smart audio chunking
                # ----------------------------------------------------------
                chunk_paths = _chunk_audio(audio_path)
                logger.info("Audio split into %d chunk(s)", len(chunk_paths))

                # ----------------------------------------------------------
                # 4d: Speech-to-text (Alibaba — placeholder)
                # ----------------------------------------------------------
                chunk_transcripts: list[str] = []
                for chunk_path in chunk_paths:
                    chunk_text = _transcribe_audio_chunk(chunk_path)
                    chunk_transcripts.append(chunk_text)

                # Reassemble and remove overlap duplicates
                transcript = _remove_boundary_duplicates(chunk_transcripts)

            finally:
                # Always remove the entire tmp directory with all audio files
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    tmp_dir = None  # prevent double-cleanup in outer finally

        # ------------------------------------------------------------------
        # 5: Summarize with Qwen
        # ------------------------------------------------------------------
        logger.info(
            "Summarizing transcript for job %s (%d chars)", job_id, len(transcript)
        )
        summary = _summarize_transcript(transcript)

        db.update_transcript_job(
            job_id, status="done", transcript=transcript, summary=summary
        )
        logger.info("Transcript job %s completed successfully", job_id)

    except Exception as exc:
        logger.exception("Transcript job %s failed: %s", job_id, exc)
        db.update_transcript_job(job_id, status="error", error_message=str(exc))
    finally:
        # Safety net: clean up tmp_dir if inner finally didn't run
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
