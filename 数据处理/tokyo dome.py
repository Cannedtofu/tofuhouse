from bs4 import BeautifulSoup
from datetime import datetime
import re
import csv
from pathlib import Path



def normalize_date(date_str, year):
    """
    'January 19th' -> 'YYYY-MM-DD'
    """
    date_str = date_str.strip()
    date_str = re.sub(r"(st|nd|rd|th)", "", date_str.lower())
    dt = datetime.strptime(f"{date_str} {year}", "%B %d %Y")
    return dt.strftime("%Y-%m-%d")


def parse_html(html):
    soup = BeautifulSoup(html, "html.parser")
    records = []

    accordion_blocks = soup.select("dl.c-mod-accordion")

    for block in accordion_blocks:
        # ---- YEAR ----
        dt = block.find("dt", class_="c-mod-accordion__head")
        if not dt:
            continue

        year_text = dt.get_text(strip=True)
        if not year_text.isdigit():
            continue

        year = int(year_text)

        # ---- TABLE ROWS ----
        rows = block.select("dd table tbody tr")

        for row in rows:
            th = row.find("th")
            td = row.find("td")

            if not th or not td:
                continue

            raw_date = th.get_text(strip=True)
            artist = td.get_text(" ", strip=True)

            if not raw_date or not artist:
                continue

            try:
                iso_date = normalize_date(raw_date, year)
            except Exception:
                # skip malformed rows like 月日
                continue

            records.append({
                "date": iso_date,
                "raw_date": raw_date,
                "artist": artist,
                "year": year
            })

    return records


def save_csv(rows, filepath):
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "raw_date", "artist", "year"]
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    base_dir = Path(__file__).parent

    input_file = base_dir / "东京巨蛋-251126.txt"
    output_file = base_dir / "tokyo_dome_events.csv"

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    with open(input_file, "r", encoding="utf-8") as f:
        html = f.read()

    records = parse_html(html)
    save_csv(records, output_file)

    print(f"✅ Saved {len(records)} rows to {output_file}")
