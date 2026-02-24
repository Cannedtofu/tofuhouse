import feedparser
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import smtplib
import ssl
from email.message import EmailMessage
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import trafilatura
import requests

# Load environment variables from .env file for local development
load_dotenv()

# --- Configuration ---
# Add your RSS feed URLs here
RSS_FEEDS = [
    "http://karpathy.github.io/feed.xml",
    "https://simonwillison.net/atom/everything/",
    "https://openai.com/news/rss.xml"
]

HISTORY_FILE = "history.json"
MAX_ARTICLES_PER_RUN = 10
DATE_RANGE_DAYS = 7
CONTENT_LENGTH_THRESHOLD = 500 # Characters

# --- Safety & Security: Load credentials from environment variables ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

    if not all([GEMINI_API_KEY, SMTP_USER, SMTP_PASSWORD, RECIPIENT_EMAIL]):
        raise ValueError("One or more required environment variables are not set.")
except (ValueError, TypeError) as e:
    print(f"Error: {e}")
    exit(1)

# --- Gemini API Configuration ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash-latest')

# --- Helper Functions ---
def fetch_full_content(url):
    """Fetches and extracts the main content from a URL using trafilatura."""
    try:
        # Use a session for better connection management and set a user-agent
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'})
        
        # Download with a reasonable timeout
        response = session.get(url, timeout=20)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # Extract content using trafilatura
        content = trafilatura.extract(response.text, favor_precision=True, include_comments=False, include_tables=False)
        return content
    except requests.exceptions.RequestException as e:
        print(f"Error fetching URL {url}: {e}")
    except Exception as e:
        print(f"Error processing URL {url} with trafilatura: {e}")
    return None

# --- Classes for Modularity ---
class Article:
    """Standardized representation of a news article from any source."""
    def __init__(self, title, link, content, published_date, source_name):
        self.title = title
        self.link = link
        self.content = content
        self.published_date = published_date  # datetime object
        self.source_name = source_name

    def get_formatted_date(self):
        if self.published_date:
            return self.published_date.strftime('%Y-%m-%d')
        return "Unknown"

class RSSSource:
    """Handles fetching and parsing articles from an RSS feed."""
    def __init__(self, feed_url):
        self.feed_url = feed_url

    def fetch(self, days_lookback=7):
        articles = []
        try:
            feed = feedparser.parse(self.feed_url)
            if not hasattr(feed.feed, 'title'):
                print(f"Skipping feed {self.feed_url} because it has no title.")
                return []
            
            feed_title = feed.feed.title
            cutoff_date = datetime.utcnow() - timedelta(days=days_lookback)

            for entry in feed.entries:
                if not all(hasattr(entry, attr) for attr in ['link', 'title']):
                    continue
                
                # Date Logic
                published_time = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if not published_time:
                    continue
                
                article_dt = datetime(*published_time[:6])
                if article_dt < cutoff_date:
                    continue

                # Content Logic
                content_text = ""
                if hasattr(entry, 'content') and entry.content:
                    content_text = entry.content[0].value
                elif hasattr(entry, 'summary'):
                    content_text = entry.summary
                
                # If content is short (likely a summary), fetch the full page
                if len(content_text) < CONTENT_LENGTH_THRESHOLD:
                    print(f"Content for '{entry.title}' is short ({len(content_text)} chars). Fetching full article from {entry.link}...")
                    full_content = fetch_full_content(entry.link)
                    if full_content:
                        content_text = full_content
                    else:
                        print(f"Failed to fetch full content for '{entry.title}'. Using summary.")

                articles.append(Article(
                    title=entry.title,
                    link=entry.link,
                    content=content_text,
                    published_date=article_dt,
                    source_name=feed_title
                ))
        except Exception as e:
            print(f"Error fetching feed {self.feed_url}: {e}")
        
        return articles

def load_history():
    """Loads the history of processed article URLs from a JSON file."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def save_history(processed_urls):
    """Saves the updated list of processed URLs to a JSON file."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(processed_urls, f, indent=4)

def summarize_with_gemini(article_title, article_content):
    """Generates a summary for a given article using the Gemini API."""
    prompt = f"""
    As an expert analyst, please provide a concise and objective summary of the following article.
    Focus on the key information and main points. Do not add any personal opinions or interpretations.

    **Article Title:** {article_title}

    **Article Content:**
    {article_content[:8000]}

    **Summary:**
    """
    
    try:
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        generation_config = {
            "temperature": 0.2,
            "max_output_tokens": 2048,
        }

        response = model.generate_content(
            contents=prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        return response.text.strip()
    except Exception as e:
        print(f"Error generating summary for '{article_title}': {e}")
        return None
    
def main():
    """Main function to run the RSS news agent."""
    print("Starting the news agent...")
    processed_urls = load_history()
    new_articles = []

    sources = [RSSSource(url) for url in RSS_FEEDS]

    print(f"Fetching articles from {len(sources)} sources...")
    for source in sources:
        if len(new_articles) >= MAX_ARTICLES_PER_RUN:
            break
        
        fetched_articles = source.fetch(DATE_RANGE_DAYS)
        
        for article in fetched_articles:
            if len(new_articles) >= MAX_ARTICLES_PER_RUN:
                break
            
            if article.link in processed_urls:
                continue
            
            new_articles.append(article)
            # We add to processed_urls here to prevent re-processing in the same run
            # and to ensure it's saved even if summarization or email fails.
            processed_urls.append(article.link)

    if not new_articles:
        print("No new articles found. Exiting.")
        return

    print(f"Found {len(new_articles)} new articles. Generating summaries...")
    markdown_content = f"# Daily News Digest - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    summaries_generated = 0

    for article in new_articles:
        if not article.content or not article.content.strip():
            print(f"Skipping article '{article.title}' because it has no content.")
            continue
        
        summary = summarize_with_gemini(article.title, article.content)
        
        if summary:
            markdown_content += f"## {article.title}\n\n"
            markdown_content += f"**Source:** `{article.source_name}`  \n"
            markdown_content += f"**Link:** {article.link}\n\n"
            markdown_content += f"{summary}\n\n---\n\n"
            summaries_generated += 1

    if summaries_generated == 0:
        print("Could not generate any summaries. Exiting.")
        save_history(processed_urls)
        return

    print(f"Generated {summaries_generated} summaries. Sending email...")
    try:
        msg = EmailMessage()
        msg.set_content(markdown_content)
        msg['Subject'] = f"News Digest: {summaries_generated} New Summaries for {datetime.now().strftime('%Y-%m-%d')}"
        msg['From'] = SMTP_USER
        msg['To'] = RECIPIENT_EMAIL

        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else: # For STARTTLS
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        print("Email sent successfully.")
    except Exception as e:
        print(f"Error sending email: {e}")
        # We still save history even if email fails to avoid summarizing again.
        # The user can re-run or handle the unsent content.
    
    print("Updating history...")
    save_history(processed_urls)
    print("News agent run completed successfully.")

if __name__ == "__main__":
    main()
