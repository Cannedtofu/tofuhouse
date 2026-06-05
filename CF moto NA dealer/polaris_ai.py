import os
import re
import time
import logging
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def setup_driver():
    """Initialize and return a Chrome WebDriver with optimized settings"""
    chrome_options = Options()
    
    # Essential options for stability
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--log-level=3")
    
    # Anti-detection measures
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # SSL and certificate handling
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--allow-running-insecure-content")
    chrome_options.add_argument("--disable-web-security")
    
    # Performance optimizations
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")  # Optional: disable images for faster loading
    
    # User agent
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        # Try using webdriver-manager first (recommended)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logger.info("Driver initialized with webdriver-manager")
    except Exception as e:
        logger.warning(f"webdriver-manager failed: {e}")
        try:
            # Fallback to local driver path based on existing working script
            service = Service(r'C:\Program Files\python chrome\chrome-win\chromedriver.exe')
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("Driver initialized with local chromedriver")
        except Exception as e:
            logger.error(f"Failed to initialize driver: {e}")
            raise
    
    # Configure timeouts
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)
    
    # Execute script to hide automation indicators
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def safe_get_page(driver, url, max_retries=3):
    """Safely get a page with retry logic"""
    for attempt in range(max_retries):
        try:
            logger.info(f"Loading URL (attempt {attempt + 1}): {url}")
            driver.get(url)
            
            # Wait for page to load - body tag present
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3) # Extra buffer for dynamic content matching human behavior
            return driver.page_source
            
        except TimeoutException:
            logger.warning(f"Timeout loading {url} on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            else:
                logger.error(f"Failed to load {url} after {max_retries} attempts.")
                try:
                    return driver.page_source
                except Exception:
                    return None
                
        except WebDriverException as e:
            logger.error(f"WebDriver error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            else:
                try:
                    return driver.page_source
                except Exception:
                    return None

# Mapping column formats for NA channel total.xlsx
COLUMNS = ["数据时间", "计数", "品牌", "网址", "地址", "邮编", "州", "经销商名称", "城市名称", "状态", "RZR", "GENERAL", "RANGER", "SportsmanandACE", "Motorcycle", "ATV", "Side x Side"]

def extract_brands(brands_text):
    """Parses text to match Polaris' array of specific keywords corresponding to the excel structure"""
    brands_upper = str(brands_text).upper()
    return {
        'RZR': 'RZR' if 'RZR' in brands_upper else '',
        'GENERAL': 'GENERAL' if 'GENERAL' in brands_upper else '',
        'RANGER': 'RANGER' if 'RANGER' in brands_upper else '',
        'SportsmanandACE': 'SportsmanandACE' if ('SPORTSMAN' in brands_upper or 'ACE' in brands_upper) else '',
        'Motorcycle': 'Motorcycle' if any(b in brands_upper for b in ['MOTORCYCLE', 'SLINGSHOT', 'INDIAN']) else '',
        'ATV': 'ATV' if 'ATV' in brands_upper else '',
        'Side x Side': 'Side x Side' if ('SIDE' in brands_upper or 'SXS' in brands_upper) else ''
    }

def process_polaris_logic():
    driver = setup_driver()
    output_path = r"D:\代码项目\CF moto NA dealer\polaris_results.xlsx"
    
    # Store visited URLs to guarantee NO DUPLICATES
    visited = set()
    dealers_data = []
    
    # Breadth/Depth Queue Queue Structure: (URL, PageType, MetaData)
    urls_to_process = [('https://www.polaris.com/en-us/off-road/dealers/', 'root', {})]
    
    try:
        while urls_to_process:
            url, page_type, meta = urls_to_process.pop(0)
            
            # Normalize url for dedup tracking
            base_url = url.split("?")[0].strip()
            is_retry = meta.get('is_retry', False)
            
            if base_url in visited and not is_retry:
                continue
            if not is_retry:
                visited.add(base_url)
            
            page_source = safe_get_page(driver, url)
            if not page_source:
                continue
                
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Phase 1: Retrieve all state links
            if page_type == 'root':
                items = soup.find_all('a', {'class': lambda c: c and 'state' in c})
                for item in items:
                    href = item['href']
                    full_url = 'https://www.polaris.com' + href if href.startswith('/') else href
                    urls_to_process.append((full_url, 'state_list', {}))
                    
            # Phase 2: Handle Lists - state lists OR secondary city lists
            elif page_type in ('state_list', 'secondary_list', 'unknown'):
                dealer_items = soup.find_all('div', {'class': "dealer-item"})
                
                if dealer_items:
                    # Page contains a list of sub-links
                    for dealer in dealer_items:
                        a_tag = dealer.find('a')
                        if not a_tag or not a_tag.has_attr('href'): continue
                        
                        href = a_tag['href']
                        full_url = 'https://www.polaris.com' + href if href.startswith('/') else href
                        
                        name_tag = dealer.find('div', {'class': 'dealer-name'})
                        city_tag = dealer.find('a', {'class': 'dealer-city'})
                        
                        name = name_tag.get_text(strip=True) if name_tag else meta.get('name', '')
                        city = city_tag.get_text(strip=True) if city_tag else meta.get('city', '')
                        
                        # Guess whether the target is a profile or a secondary list
                        # E.g. /dealers/tx/houston/123000/ -> length implies a specific profile 
                        # E.g. /dealers/tx/houston/ -> secondary list
                        next_type = 'unknown' 
                        if full_url not in visited:
                            urls_to_process.append((full_url, next_type, {'name': name, 'city': city}))
                else: 
                    # No dealer items listed -> it is likely an actual profile page resolving the "unknown"
                    if page_type == 'unknown':
                        page_type = 'profile'
            
            # Phase 3: Final execution for a resolved direct Dealer Profile
            if page_type == 'profile':
                address_tag = soup.find('address')
                brands_div = soup.find('div', class_='multiple-brands-support')
                
                # Default logic fallback
                detail_address = address_tag.get_text(strip=True) if address_tag else ""
                brands_text = brands_div.get_text(separator=', ', strip=True) if brands_div else ""
                
                if not detail_address:
                    retries = meta.get('retries', 0)
                    if retries < 3:
                        logger.warning(f"Address not found for {url}, requeuing for retry {retries + 1}")
                        meta['retries'] = retries + 1
                        meta['is_retry'] = True
                        # Put it at the end of the queue to try later
                        urls_to_process.append((url, 'profile', meta))
                        continue
                    else:
                        logger.error(f"Failed to get address for {url} after 3 retries, marking as N/A")
                        detail_address = "N/A"
                
                # Fetch Name if nested explicitly
                name_tag = soup.find('h1', itemprop='name') or soup.find('h1')
                final_name = name_tag.get_text(strip=True) if name_tag else meta.get('name', 'N/A')
                
                # Regex out State Code and Zip Code from Address block 
                # (ex. "123 Main St, Waco, TX 76706")
                final_state = meta.get('state', '')
                final_zip = ''
                match = re.search(r'\b([A-Z]{2})\s*(\d{5}(?:-\d{4})?)\s*$', detail_address)
                if match:
                    final_state = match.group(1)
                    final_zip = match.group(2)
                
                brands_mapped = extract_brands(brands_text)
                
                data_row = {
                    '数据时间': datetime.now().strftime('%m/%d/%Y'),
                    '计数': 'Y',
                    '品牌': 'Polaris',
                    '网址': base_url,
                    '地址': detail_address,
                    '邮编': final_zip,
                    '州': final_state,
                    '经销商名称': final_name,
                    '城市名称': meta.get('city', ''),
                    '状态': '',
                    **brands_mapped  # Spreads RZR, GENERAL, RANGER etc.
                }
                
                dealers_data.append(data_row)
                logger.info(f"---> Successfully Extracted Profile: {final_name} | {final_state}")
                
                # Auto-save pipeline: Periodically save progress out
                if len(dealers_data) % 50 == 0:
                    pd.DataFrame(dealers_data, columns=COLUMNS).to_excel(output_path, index=False)
                    logger.info(f"Progress Checkpoint: Data saved ({len(dealers_data)} records).")

    except KeyboardInterrupt:
        logger.warning("Scraping manually stopped. Saving partial data...")
    finally:
        # End of Execution Saving Procedure
        if dealers_data:
            df = pd.DataFrame(dealers_data, columns=COLUMNS)
            df.to_excel(output_path, index=False)
            logger.info(f"COMPLETE! All {len(dealers_data)} extracted dealers successfully saved into {output_path}")
        else:
            logger.warning("No dealers were extracted.")
            
        driver.quit()

if __name__ == "__main__":
    logger.info("Initializing 1-step intelligent scraper logic for Polaris...")
    process_polaris_logic()
