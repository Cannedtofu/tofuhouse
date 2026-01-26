import requests
from bs4 import BeautifulSoup
import csv
import time
import random
import urllib.parse
import os

def get_processed_cities(filename):
    """Checks the CSV and returns a set of city names already scraped."""
    if not os.path.exists(filename):
        return set()
    processed = set()
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('City'):
                    processed.add(row['City'].lower())
    except Exception as e:
        print(f"Note: Could not read existing data: {e}")
    return processed

def update_error_log(failed_list, log_file='failed_urls.txt'):
    """Writes the current list of failed URLs to a text file."""
    with open(log_file, 'w', encoding='utf-8') as f:
        for url, city, start in failed_list:
            f.write(f"{url}|{city}|{start}\n")

def parse_page_data(soup, city_name, current_url, start_url):
    """Extracts concert items from a page."""
    data = []
    concert_items = soup.select('div.recent ul.cityconcerts li .event_box')
    path_parts = current_url.strip('/').split('/')
    display_page = path_parts[-1] if path_parts[-1].isdigit() else "1"

    if concert_items:
        for item in concert_items:
            title_tag = item.find('h3')
            link_tag = item.find('a', class_='sellurl')
            details_ul = item.find('ul')
            
            time_val, venue_val, price_val = "N/A", "N/A", "N/A"
            if details_ul:
                for li in details_ul.find_all('li'):
                    text = li.get_text(strip=True)
                    if "时间" in text: time_val = text.replace("演出时间", "").strip()
                    elif "场馆" in text: venue_val = text.replace("演出场馆", "").strip()
                    elif "价格" in text:
                        price_em = li.find('em', class_='price')
                        if price_em: price_val = price_em.get_text(strip=True)

            data.append({
                "City": city_name,
                "Page_Number": display_page,
                "Title": title_tag.get_text(strip=True) if title_tag else "N/A", 
                "Time": time_val, "Venue": venue_val, "Price_Yuan": price_val, 
                "URL": urllib.parse.urljoin(start_url, link_tag['href']) if link_tag else "N/A"
            })
    return data

def scrape_multi_city_concerts(input_file='cities.txt'):
    output_filename = 'concerts_data_combined.csv'
    error_log_file = 'failed_urls.txt'
    existing_cities = get_processed_cities(output_filename)
    
    headers = {"User-Agent": "Mozilla/5.0...", "Referer": "https://www.pythonke.com/"}
    session = requests.Session()
    session.headers.update(headers)
    fields = ["City", "Page_Number", "Title", "Time", "Venue", "Price_Yuan", "URL"]
    
    if not os.path.exists(output_filename):
        with open(output_filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()

    with open(input_file, 'r') as f:
        target_urls = [line.strip() for line in f if line.strip()]

    global_failed_urls = [] 
    temp_buffer = []
    total_processed_pages = 0

    # --- ROUND 1: Initial Pass ---
    for start_url in target_urls:
        city_name = start_url.strip('/').split('/')[-1].lower()
        if city_name in existing_cities:
            print(f">>> Skipping {city_name.upper()}")
            continue

        urls_to_scrape = [start_url]
        scraped_urls = set()

        while urls_to_scrape:
            current_url = urls_to_scrape.pop(0)
            total_processed_pages += 1
            try:
                print(f"[{city_name}] Page {total_processed_pages} | Scraping: {current_url}")
                response = session.get(current_url, timeout=30)
                if response.status_code != 200:
                    global_failed_urls.append((current_url, city_name, start_url))
                    update_error_log(global_failed_urls) # Save to file immediately
                    continue
                    
                soup = BeautifulSoup(response.text, 'html.parser')
                temp_buffer.extend(parse_page_data(soup, city_name, current_url, start_url))

                if total_processed_pages % 10 == 0 and temp_buffer:
                    with open(output_filename, 'a', newline='', encoding='utf-8-sig') as f:
                        writer = csv.DictWriter(f, fieldnames=fields)
                        writer.writerows(temp_buffer)
                    print(f"--- Buffer Flushed: {len(temp_buffer)} items saved. ---")
                    temp_buffer = []

                next_tag = soup.find('a', string="»")
                if next_tag and next_tag.get('href'):
                    next_url = urllib.parse.urljoin(start_url, next_tag.get('href'))
                    if next_url not in scraped_urls and next_url not in urls_to_scrape:
                        urls_to_scrape.append(next_url)
                scraped_urls.add(current_url)
                time.sleep(random.uniform(1, 2))
            except Exception as e:
                global_failed_urls.append((current_url, city_name, start_url))
                update_error_log(global_failed_urls)

    # --- ROUND 2: Final Retry ---
    if global_failed_urls:
        print(f"\n--- STARTING RETRY ROUND: {len(global_failed_urls)} pages ---")
        retry_queue = global_failed_urls.copy()
        
        for item in retry_queue:
            url, city, start = item
            try:
                response = session.get(url, timeout=30)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    temp_buffer.extend(parse_page_data(soup, city, url, start))
                    # SUCCESS: Remove from the global failure list
                    global_failed_urls.remove(item)
                    update_error_log(global_failed_urls)
                    print(f"[SUCCESS] {url} retrieved on retry.")
                time.sleep(random.uniform(1, 2))
            except:
                print(f"[STILL FAILED] {url}")

    if temp_buffer:
        with open(output_filename, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writerows(temp_buffer)

    print(f"\nTask Complete. Final errors (if any) are in {error_log_file}")

if __name__ == "__main__":
    scrape_multi_city_concerts('cities.txt')