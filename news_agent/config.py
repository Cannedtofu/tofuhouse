import os
from dotenv import load_dotenv

load_dotenv()

# --- Nitter instances (tried in order until one works) ---
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

# --- LLM (Qwen via Alibaba Cloud DashScope, OpenAI-compatible) ---
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_SUMMARY_MODEL = "qwen-plus"        # fast + cheap, used for article summaries
QWEN_VISION_MODEL = "qwen-vl-max"       # vision model, used for browser-use fallback only
MIN_BROWSER_FALLBACK_CHARS = 300        # Playwright result shorter than this triggers agent fallback

# --- Database ---
DB_PATH = os.getenv("DB_PATH", "news.db")

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

# --- Email ---
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
