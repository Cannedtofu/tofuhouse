import os
from bs4 import BeautifulSoup
from openpyxl import Workbook
from datetime import datetime, timedelta
import re

# ----------------- CONFIG -----------------
INPUT_FILE = r"D:\远程云盘\SynologyDrive\麦星(远程)\研究\IP娱乐\popmart youtube - 251118.txt"
OUTPUT_FILE = r"D:\远程云盘\SynologyDrive\麦星(远程)\研究\IP娱乐\popmart_youtube_output.xlsx"

# ----------------- FUNCTIONS -----------------

def convert_relative_time(text):
    """
    Convert '3 days ago', '5 months ago', etc. → 'YYYY-MM'.
    If the format is unknown, return None.
    """
    if not text:
        return None

    text = text.strip().lower()
    match = re.match(r"(\d+)\s+(day|week|month|year)s?\s+ago", text)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)
    now = datetime.now()

    if unit == "day":
        dt = now - timedelta(days=value)
    elif unit == "week":
        dt = now - timedelta(weeks=value)
    elif unit == "month":
        # Approximate month subtraction
        year = now.year
        month = now.month - value
        while month <= 0:
            month += 12
            year -= 1
        dt = datetime(year, month, 1)
    elif unit == "year":
        dt = datetime(now.year - value, now.month, 1)
    else:
        return None

    return dt.strftime("%Y-%m")


def parse_youtube_items(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("ytd-rich-item-renderer")
    results = []

    for item in items:
        # --- Video URL ---
        link_tag = item.select_one("a#thumbnail[href]")
        video_url = link_tag["href"] if link_tag else None

        # --- Thumbnail URL (robust) ---
        thumb_img = item.select_one("ytd-thumbnail img")
        thumbnail_url = None
        if thumb_img:
            for attr in ["src", "data-thumb", "data-ytimg", "srcset"]:
                if thumb_img.get(attr):
                    thumbnail_url = thumb_img.get(attr)
                    break
            # srcset cleanup
            if thumbnail_url and " " in thumbnail_url:
                thumbnail_url = thumbnail_url.split(",")[0].split()[0]

        # --- Title ---
        title_tag = item.select_one("#video-title")
        title = title_tag.get_text(strip=True) if title_tag else None

        # --- Duration ---
        duration_tag = item.select_one(
            "ytd-thumbnail-overlay-time-status-renderer .yt-badge-shape__text"
        )
        duration = duration_tag.get_text(strip=True) if duration_tag else None

        # --- Views ---
        views_tag = item.select_one("#metadata-line .inline-metadata-item:nth-of-type(1)")
        views = views_tag.get_text(strip=True) if views_tag else None

        # --- Upload time ---
        upload_tag = item.select_one("#metadata-line .inline-metadata-item:nth-of-type(2)")
        upload_time = upload_tag.get_text(strip=True) if upload_tag else None

        results.append({
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
            "title": title,
            "duration": duration,
            "views": views,
            "upload_time": upload_time,
        })

    return results


# ----------------- MAIN -----------------
if __name__ == "__main__":
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError("Input file not found: " + INPUT_FILE)

    # Read HTML content
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Parse all videos
    data = parse_youtube_items(html_content)

    # Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "YouTube Videos"

    # Headers
    headers = [
        "video_url",
        "thumbnail_url",
        "title",
        "duration",
        "views",
        "upload_time",      # original
        "upload_time_ym"    # converted
    ]
    ws.append(headers)

    # Rows
    for item in data:
        converted = convert_relative_time(item["upload_time"])
        ws.append([
            item["video_url"],
            item["thumbnail_url"],
            item["title"],
            item["duration"],
            item["views"],
            item["upload_time"],     # original
            converted                # YYYY-MM
        ])

    wb.save(OUTPUT_FILE)
    print(f"Done. Extracted {len(data)} videos.")
    print("Saved Excel to:", OUTPUT_FILE)
