import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
import openpyxl
from openpyxl import Workbook

# Base URL
BASE_URL = "https://www.pythonke.com/concerts"

# Headers to mimic a browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

# List of Chinese cities based on the website structure
CITIES = [
    'beijing', 'shanghai', 'guangzhou', 'shenzhen', 'chengdu', 
    'hangzhou', 'chongqing', 'wuhan', 'xian', 'suzhou', 
    'tianjin', 'nanjing', 'changsha', 'zhengzhou', 'dongguan', 
    'qingdao', 'shenyang', 'ningbo', 'kunming', 'wuxi', 
    'foshan', 'hefei', 'dalian', 'fuzhou', 'xiamen', 
    'harbin', 'jinan', 'wenzhou', 'nanning', 'changchun', 
    'quanzhou', 'shijiazhuang', 'guiyang', 'changzhou', 'nantong', 
    'jiaxing', 'taiyuan', 'xuzhou', 'nanchang', 'huizhou', 
    'zhuhai', 'zhongshan', 'yantai', 'lanzhou', 'shaoxing', 
    'haikou', 'yangzhou', 'shantou'
]


def get_page(url, retries=3, delay=2):
    """
    Fetch a page with retry logic and error handling.
    """
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"  Error fetching {url} (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                return None
    return None


def find_pagination_pattern(soup, base_url):
    """
    Detect pagination pattern and return all page URLs for a city.
    Returns list of URLs to scrape.
    """
    page_urls = [base_url]  # Always include first page
    
    # Try to find pagination elements
    # Common patterns: pagination div, next links, page numbers
    pagination = soup.find('div', class_=re.compile('pagination|page', re.I))
    if not pagination:
        # Try to find next page link anywhere
        next_link = soup.find('a', string=re.compile('下一页|next|›|>', re.I))
        if next_link and next_link.get('href'):
            # If we found a next link, try to determine pagination pattern
            next_href = next_link.get('href')
            if 'page=' in next_href or 'p=' in next_href:
                # Query parameter pagination
                page_num = 2
                while True:
                    if 'page=' in base_url:
                        url = re.sub(r'page=\d+', f'page={page_num}', base_url)
                    elif 'p=' in base_url:
                        url = re.sub(r'p=\d+', f'p={page_num}', base_url)
                    else:
                        separator = '&' if '?' in base_url else '?'
                        url = f"{base_url}{separator}page={page_num}"
                    
                    html = get_page(url)
                    if not html:
                        break
                    soup_page = BeautifulSoup(html, 'html.parser')
                    if not soup_page.find('ul', class_='cityconerts row'):
                        break
                    page_urls.append(url)
                    page_num += 1
                    time.sleep(1)
            elif '/page/' in next_href or '/p/' in next_href:
                # Path-based pagination
                page_num = 2
                while True:
                    url = urljoin(base_url, f'/page/{page_num}/')
                    html = get_page(url)
                    if not html:
                        break
                    soup_page = BeautifulSoup(html, 'html.parser')
                    if not soup_page.find('ul', class_='cityconerts row'):
                        break
                    page_urls.append(url)
                    page_num += 1
                    time.sleep(1)
    else:
        # Find all page links in pagination
        page_links = pagination.find_all('a', href=True)
        max_page = 1
        for link in page_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)
            # Try to extract page number
            if text.isdigit():
                max_page = max(max_page, int(text))
            elif 'page=' in href or 'p=' in href:
                match = re.search(r'[pP]age[=_](\d+)|[pP][=_](\d+)', href)
                if match:
                    page_num = int(match.group(1) or match.group(2))
                    max_page = max(max_page, page_num)
        
        # Generate all page URLs
        if max_page > 1:
            for page in range(2, max_page + 1):
                if '?' in base_url:
                    url = f"{base_url}&page={page}"
                else:
                    url = f"{base_url}?page={page}"
                page_urls.append(url)
    
    return page_urls


def parse_concert_entry(li_element, city_name, source_url):
    """
    Parse a single concert entry from an <li> element.
    Extracts all available information.
    """
    concert_data = {
        'city': city_name,
        'source_url': source_url,
        'title': '',
        'artist': '',
        'date': '',
        'time': '',
        'venue': '',
        'price': '',
        'ticket_link': '',
        'image_url': '',
        'description': '',
        'raw_text': ''
    }
    
    try:
        # Get all text content
        raw_text = li_element.get_text(separator=' ', strip=True)
        concert_data['raw_text'] = raw_text
        
        # Find title/artist - usually in h2, h3, h4, or strong tags
        title_elem = (li_element.find('h2') or li_element.find('h3') or 
                     li_element.find('h4') or li_element.find('strong') or
                     li_element.find('a', class_=re.compile('title|name', re.I)))
        if title_elem:
            concert_data['title'] = title_elem.get_text(strip=True)
            concert_data['artist'] = concert_data['title']  # Often the same
        
        # Find ticket link
        link_elem = li_element.find('a', href=True)
        if link_elem:
            href = link_elem.get('href', '')
            concert_data['ticket_link'] = urljoin(BASE_URL, href)
            # If no title found yet, try from link text
            if not concert_data['title']:
                concert_data['title'] = link_elem.get_text(strip=True)
        
        # Find image
        img_elem = li_element.find('img')
        if img_elem:
            img_src = img_elem.get('src') or img_elem.get('data-src')
            if img_src:
                concert_data['image_url'] = urljoin(BASE_URL, img_src)
            # Alt text might contain title
            if img_elem.get('alt') and not concert_data['title']:
                concert_data['title'] = img_elem.get('alt')
        
        # Find date/time - look for common patterns
        date_elem = (li_element.find('span', class_=re.compile('date|time', re.I)) or
                    li_element.find('div', class_=re.compile('date|time', re.I)) or
                    li_element.find('p', class_=re.compile('date|time', re.I)))
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            # Try to separate date and time
            if ' ' in date_text or '：' in date_text or ':' in date_text:
                parts = re.split(r'[：:\s]+', date_text, 1)
                concert_data['date'] = parts[0] if len(parts) > 0 else date_text
                concert_data['time'] = parts[1] if len(parts) > 1 else ''
            else:
                concert_data['date'] = date_text
        
        # Find venue
        venue_elem = (li_element.find('span', class_=re.compile('venue|location|place', re.I)) or
                     li_element.find('div', class_=re.compile('venue|location|place', re.I)) or
                     li_element.find('p', class_=re.compile('venue|location|place', re.I)))
        if venue_elem:
            concert_data['venue'] = venue_elem.get_text(strip=True)
        
        # Find price
        price_elem = (li_element.find('span', class_=re.compile('price|cost', re.I)) or
                     li_element.find('div', class_=re.compile('price|cost', re.I)) or
                     li_element.find('strong', class_=re.compile('price|cost', re.I)))
        if price_elem:
            concert_data['price'] = price_elem.get_text(strip=True)
        
        # Find description
        desc_elem = (li_element.find('p', class_=re.compile('desc|intro|summary', re.I)) or
                    li_element.find('div', class_=re.compile('desc|intro|summary', re.I)))
        if desc_elem:
            concert_data['description'] = desc_elem.get_text(strip=True)
        
        # If still no title, try to extract from raw text (first meaningful line)
        if not concert_data['title'] and raw_text:
            lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
            if lines:
                concert_data['title'] = lines[0]
        
    except Exception as e:
        print(f"    Error parsing concert entry: {e}")
    
    return concert_data


def scrape_city_concerts(city_name):
    """
    Scrape all concerts for a given city, handling pagination.
    """
    city_url = f"{BASE_URL}/{city_name}"
    print(f"\nProcessing city: {city_name} -> {city_url}")
    
    all_concerts = []
    
    # Get first page
    html = get_page(city_url)
    if not html:
        print(f"  Failed to fetch {city_url}")
        return all_concerts
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Check if concerts list exists
    concerts_list = soup.find('ul', class_='cityconerts row')
    if not concerts_list:
        print(f"  No concerts found for {city_name}")
        return all_concerts
    
    # Get all page URLs (including first page)
    page_urls = find_pagination_pattern(soup, city_url)
    print(f"  Found {len(page_urls)} page(s) for {city_name}")
    
    # Scrape each page
    for page_num, page_url in enumerate(page_urls, 1):
        print(f"    Scraping page {page_num}/{len(page_urls)}: {page_url}")
        
        # Get page HTML (already have first page)
        if page_num == 1:
            page_html = html
        else:
            page_html = get_page(page_url)
            if not page_html:
                print(f"      Failed to fetch page {page_num}")
                continue
            time.sleep(1)  # Be respectful
        
        page_soup = BeautifulSoup(page_html, 'html.parser')
        concerts_list = page_soup.find('ul', class_='cityconerts row')
        
        if not concerts_list:
            print(f"      No concerts list found on page {page_num}")
            continue
        
        # Find all concert entries (usually <li> elements)
        concert_items = concerts_list.find_all('li', recursive=False)
        if not concert_items:
            # Try to find any child elements that might be concert entries
            concert_items = concerts_list.find_all(['li', 'div'], recursive=True)
            # Filter to only direct children or items with concert-like classes
            concert_items = [item for item in concert_items 
                           if item.find_parent('ul', class_='cityconerts row') == concerts_list]
        
        print(f"      Found {len(concert_items)} concert entries")
        
        # Parse each concert entry
        for item in concert_items:
            concert_data = parse_concert_entry(item, city_name, page_url)
            all_concerts.append(concert_data)
        
        time.sleep(1)  # Delay between pages
    
    print(f"  Total concerts scraped for {city_name}: {len(all_concerts)}")
    return all_concerts


def save_to_excel(concerts, filename=None):
    """
    Save all concert data to an Excel file.
    """
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"pythonke_concerts_{timestamp}.xlsx"
    
    if not concerts:
        print("No concerts to save.")
        return
    
    # Get all unique keys from all concert dictionaries
    all_keys = set()
    for concert in concerts:
        all_keys.update(concert.keys())
    
    # Define column order (prioritize important fields)
    column_order = [
        'city', 'title', 'artist', 'date', 'time', 'venue', 
        'price', 'ticket_link', 'image_url', 'description', 
        'source_url', 'raw_text'
    ]
    
    # Add any additional keys not in the predefined order
    for key in sorted(all_keys):
        if key not in column_order:
            column_order.append(key)
    
    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Concerts"
    
    # Write headers
    ws.append(column_order)
    
    # Write data
    for concert in concerts:
        row = [concert.get(key, '') for key in column_order]
        ws.append(row)
    
    # Save file
    wb.save(filename)
    print(f"\n✅ Saved {len(concerts)} concerts to {filename}")


def main():
    """
    Main execution function.
    """
    print("=" * 60)
    print("Pythonke Concert Scraper")
    print("=" * 60)
    print(f"Scraping concerts from {BASE_URL}")
    print(f"Total cities to process: {len(CITIES)}")
    print("=" * 60)
    
    all_concerts = []
    
    for i, city in enumerate(CITIES, 1):
        print(f"\n[{i}/{len(CITIES)}] Processing {city}...")
        try:
            concerts = scrape_city_concerts(city)
            all_concerts.extend(concerts)
        except Exception as e:
            print(f"  Error processing {city}: {e}")
            continue
        
        # Small delay between cities
        time.sleep(2)
    
    # Save all results to Excel
    print("\n" + "=" * 60)
    print("Saving results to Excel...")
    save_to_excel(all_concerts)
    print("=" * 60)
    print(f"\n✅ Scraping complete! Total concerts scraped: {len(all_concerts)}")


if __name__ == "__main__":
    main()

