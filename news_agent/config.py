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

# --- LLM ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-lite-preview-09-2025"

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
