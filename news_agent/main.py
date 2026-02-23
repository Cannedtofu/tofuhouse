import feedparser
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import smtplib
import ssl
from email.message import EmailMessage
from google import genai
from google.genai import types


# Load environment variables from .env file for local development
load_dotenv()

# --- Configuration ---
# Add your RSS feed URLs here
RSS_FEEDS = [
    "http://karpathy.github.io/feed.xml",
]

HISTORY_FILE = "history.json"
MAX_ARTICLES_PER_RUN = 1

# --- Safety & Security: Load credentials from environment variables ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

    if not all([GEMINI_API_KEY, SMTP_USER, SMTP_PASSWORD, RECIPIENT_EMAIL]):
        raise ValueError("One or more required environment variables are not set.")
except (ValueError, TypeError) as e:
    print(f"Error: {e}")
    exit(1)

# --- Gemini API Configuration ---
client = genai.Client(api_key=GEMINI_API_KEY)

# --- Helper Functions ---

def load_history():
    """Loads the history of processed article URLs from a JSON file."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("processed_urls", [])
            return data
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
    {article_content[:4000]}

    **Summary:**
    """
    
    try:
        # 建议使用更新的基础模型，如 gemini-2.0-flash 或保持 gemini-1.5-flash
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite-preview-09-2025', 
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                top_p=1.0,
                top_k=1,
                max_output_tokens=2048,
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    ),
                ]
            )
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

    print(f"Fetching articles from {len(RSS_FEEDS)} feeds...")
    for feed_url in RSS_FEEDS:
        feed_url = feed_url.strip()
        if len(new_articles) >= MAX_ARTICLES_PER_RUN:
            break
        try:
            feed = feedparser.parse(feed_url)
            if not hasattr(feed.feed, 'title'):
                print(f"Skipping feed {feed_url} because it has no title.")
                continue
            feed_title = feed.feed.title
            for entry in feed.entries:
                if len(new_articles) >= MAX_ARTICLES_PER_RUN:
                    break
                # Skip if the article has no link or title, or if it has already been processed.
                if not all(hasattr(entry, attr) for attr in ['link', 'title']):
                    continue
                if entry.link in processed_urls:
                    continue

                new_articles.append((entry, feed_title)) # Store as a tuple
                processed_urls.append(entry.link)
        except Exception as e:
            print(f"Error fetching or parsing feed {feed_url}: {e}")

    if not new_articles:
        print("No new articles found. Exiting.")
        return

    print(f"Found {len(new_articles)} new articles. Generating summaries...")
    markdown_content = f"# Daily News Digest - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    summaries_generated = 0

    for article, feed_title in new_articles:
        # 'content' can be a list, we take the first one's value. Fallback to summary.
        content_text = ""
        if hasattr(article, 'content') and article.content:
            content_text = article.content[0].value
        elif hasattr(article, 'summary'):
            content_text = article.summary

        if not content_text.strip():
            print(f"Skipping article '{article.title}' because it has no content.")
            continue
        
        summary = summarize_with_gemini(article.title, content_text)
        
        if summary:
            markdown_content += f"## {article.title}\n\n"
            markdown_content += f"**Source:** `{feed_title}`\n\n"
            markdown_content += f"{summary}\n\n---\n\n"
            summaries_generated += 1

    if summaries_generated == 0:
        print("Could not generate any summaries. Exiting.")
        # We still save history to avoid retrying failed articles
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
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        print("Email sent successfully.")
    except Exception as e:
        print(f"Error sending email: {e}")
        # If email fails, we don't save history so we can retry this batch
        return 

    print("Updating history...")
    save_history(processed_urls)
    print("News agent run completed successfully.")


if __name__ == "__main__":
    main()
