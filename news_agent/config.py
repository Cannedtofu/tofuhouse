import os
from dotenv import load_dotenv

load_dotenv()

# --- Nitter instances (tried in order until one works) ---
# Set NITTER_LOCAL_URL=http://127.0.0.1:8080 in .env to use a self-hosted instance first
NITTER_LOCAL_URL = os.getenv("NITTER_LOCAL_URL", "")

NITTER_INSTANCES = (
    [NITTER_LOCAL_URL] if NITTER_LOCAL_URL else []
)

# --- LLM (Qwen via Alibaba Cloud DashScope, OpenAI-compatible) ---
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_SUMMARY_MODEL = "qwen-plus"        # fast + cheap, used for article summaries
QWEN_TRANSLATION_MODEL: str = os.getenv("QWEN_TRANSLATION_MODEL", "qwen-mt-lite")
ASR_MODEL: str = os.getenv("ASR_MODEL", "fun-asr")
QWEN_VISION_MODEL = "qwen3-vl-flash"       # vision model, used for browser-use fallback only
MIN_BROWSER_FALLBACK_CHARS = 300        # Playwright result shorter than this triggers agent fallback

# --- Database ---
DB_PATH = os.getenv("DB_PATH", "news.db")

# --- Scheduler + Nitter fetch window ---
# NITTER_FETCH_PERIOD_HOURS controls two things simultaneously:
#   1. How often the background scheduler runs.
#   2. The pagination stop threshold — tweets older than this many hours are skipped.
# Example: set to 12 → fetch every 12h AND stop paginating when tweet is >12h old.
NITTER_FETCH_PERIOD_HOURS = int(os.getenv("NITTER_FETCH_PERIOD_HOURS", "24"))

# Seconds to wait between Nitter HTML pagination page requests (default: 120 = 2 min).
NITTER_PAGE_DELAY = int(os.getenv("NITTER_PAGE_DELAY", "120"))

# Seconds between consecutive Nitter source fetches (to avoid X.com rate limits).
# Base value; actual delay adds 0–50% random jitter on top.
NITTER_INTER_SOURCE_DELAY = int(os.getenv("NITTER_INTER_SOURCE_DELAY", "60"))

# --- Article filtering ---
MIN_ARTICLE_DATE = "2026-01-01"          # hard floor: drop articles published before this date
DATE_RANGE_DAYS = 7                      # only fetch articles from the last N days per run
MAX_ARTICLES_PER_SOURCE = 50            # cap per source per run to avoid runaway fetches
CONTENT_LENGTH_THRESHOLD = 500           # fetch full content if RSS body is shorter
MIN_CONTENT_WORDS = 200                  # re-fetch existing articles whose stored content is shorter than this

# --- Default sources (seeded into DB on first run if no sources exist) ---
DEFAULT_SOURCES = [
    {
        "name": "OpenAI",
        "type": "rss",
        "url": "https://openai.com/news/rss.xml",
        "url_filter": "openai.com/index/",   # only keep articles whose URL contains this
    },
]

# --- Flask session ---
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
REPORT_API_KEY = os.getenv("REPORT_API_KEY", "")

# --- Access whitelist (comma-separated emails; empty = open to all) ---
EMAIL_WHITELIST: list[str] = [
    e.strip().lower()
    for e in os.getenv("EMAIL_WHITELIST", "").split(",")
    if e.strip()
]

# --- Admin account (full source management including delete) ---
ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "cuiyuan@maisoncapital.com").strip().lower()

# --- YouTube cookies file (Netscape format) for yt-dlp authentication ---
# Only needed when the fast path (youtube-transcript-api) fails, i.e. for videos
# with no English captions. Set to the path of a manually exported cookies file.
YOUTUBE_COOKIES_FILE: str = os.getenv("YOUTUBE_COOKIES_FILE", "")

# --- SOCKS5 proxy for YouTube transcript requests only ---
# Used exclusively by transcript_worker.py to route yt-dlp through Mihomo.
# Does not affect any other outbound connections in the app.
# Example: socks5://127.0.0.1:7890
SOCKS_PROXY: str = os.getenv("SOCKS_PROXY", "")

# --- Audio cache for transcript jobs ---
# Audio files are moved here after download so they survive transcription failures.
# Deleted automatically after successful transcription.
AUDIO_CACHE_DIR: str = os.getenv(
    "AUDIO_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_cache"),
)

# --- Public base URL of this app (used to serve audio to DashScope) ---
# DashScope's cloud ASR cannot access local file:// paths; audio must be served
# over HTTP. Set to the server's public URL, e.g. http://47.239.66.248
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://47.239.66.248")

# --- Email ---
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
