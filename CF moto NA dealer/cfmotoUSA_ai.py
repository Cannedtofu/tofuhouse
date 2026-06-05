import os
import re
import time
import logging
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# List of 50 US States to loop through for full coverage
STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", 
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", 
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", 
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", 
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", 
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", 
    "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"
]

COLUMNS = ["数据时间", "计数", "品牌", "网址", "地址", "邮编", "州", "经销商名称", "城市名称", "状态", "RZR", "GENERAL", "RANGER", "SportsmanandACE", "Motorcycle", "ATV", "Side x Side"]

def setup_driver():
    """Initialize and return an undetected Chrome WebDriver to bypass bot detection"""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")

    # uc patches the driver binary and JS environment automatically —
    # no need to manually hide navigator.webdriver or excludeSwitches
    driver = uc.Chrome(options=options, headless=False)
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)
    logger.info("undetected-chromedriver initialized")
    return driver

def scrape_cfmoto():
    driver = setup_driver()
    output_path = r"D:\代码项目\CF moto NA dealer\CFmoto_results.xlsx"
    
    seen_dealers = set()  # Dedup by name + zipcode
    results = []
    
    try:
        logger.info("Loading CFMoto Dealer Locator page...")
        driver.get('https://www.cfmotousa.com/dealer-locator')
        
        # Wait extensively for Cloudflare challenge + widget load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, '//input[@type="text" or contains(@placeholder, "city") or contains(@placeholder, "zip")]'))
        )
        time.sleep(3) # Small buffer for JS to attach listeners
        
        for state in STATES:
            logger.info(f"--- Searching for State: {state} ---")
            
            # 1. Find and interact with search box
            try:
                # Target the input box dynamically
                search_input = driver.find_element(By.XPATH, '//input[contains(@placeholder, "city") or contains(@placeholder, "zip code") or contains(@id, "lad__")]')
            except:
                # Fallback to any generic text input if specific ids disappeared
                search_input = driver.find_element(By.CSS_SELECTOR, 'input[type="text"]')
                
            search_input.clear()
            search_input.send_keys(Keys.CONTROL + "a")
            search_input.send_keys(Keys.DELETE)
            time.sleep(0.5)
            
            search_input.send_keys(state)
            time.sleep(0.5)
            search_input.send_keys(Keys.ENTER)
            
            # Or try finding the search button
            try:
                search_btn = driver.find_element(By.XPATH, '//button[contains(translate(text(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "search")]')
                driver.execute_script("arguments[0].click();", search_btn)
            except:
                pass # ENTER likely worked
                
            # Wait for results to update via XHR
            time.sleep(5)
            
            # 2. Extract lad__location-list
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            result_list = soup.find('ul', id='lad__location-list')
            
            if not result_list:
                # If they updated ID, fallback to searching for lists
                result_list = soup.find('ul', class_=lambda c: c and 'location-list' in c)
                
            if not result_list:
                logger.warning(f"No results found or lad__location-list hidden for {state}")
                continue
                
            # 3. Parse individual dealers
            # Look for explicit lad items, or fallback to generic LIs
            items = result_list.find_all('li', class_=lambda c: c and ('lad__' in c or 'location' in c))
            if not items:
                items = result_list.find_all('li')
                
            logger.info(f"Extracted {len(items)} dealer cards from layout.")
            
            for index, li in enumerate(items):
                # Full string text block to parse cleanly
                text_block = li.get_text(separator='\n', strip=True)
                
                # --- A. Dealer Name ---
                name = ""
                # Try locating by common lad__ prefixed classes or standard header tags
                name_tag = li.find(class_=lambda c: c and 'name' in c) or li.find(['h2', 'h3', 'h4', 'strong'])
                if name_tag:
                    name = name_tag.get_text(strip=True)
                else:
                    name = text_block.split('\n')[0] if text_block else "N/A"
                    
                # --- B. Address parsing ---
                address = ""
                addr_tag = li.find(class_=lambda c: c and 'address' in c) or li.find('address')
                if addr_tag:
                    address = addr_tag.get_text(separator=', ', strip=True)
                else:
                    # Fallback to the second line of text, guessing it's the address
                    lines = text_block.split('\n')
                    address = ", ".join(lines[1:3]) if len(lines) > 2 else lines[1]
                
                # --- C. State and Zip Regex Extractor ---
                st = ""
                zipcode = ""
                match = re.search(r'\b([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b', address.replace(',', ' '))
                if match:
                    st = match.group(1)
                    zipcode = match.group(2)
                    
                # Deduplication Anchor
                unique_key = f"{name}_{zipcode}".lower()
                if unique_key in seen_dealers:
                    continue
                seen_dealers.add(unique_key)
                
                # --- D. Links / Website ---
                dealer_url = ""
                website_btn = li.find('a', string=lambda s: s and 'Website' in s) or li.find('a', class_=lambda c: c and 'website' in c)
                if website_btn and website_btn.has_attr('href'):
                    dealer_url = website_btn['href']
                
                # --- E. Brands / Capabilities ---
                brands_upper = text_block.upper()
                has_motorcycle = 'Motorcycle' if 'MOTORCYCLE' in brands_upper else ''
                has_atv = 'ATV' if 'ATV' in brands_upper else ''
                has_sxs = 'Side x Side' if any(b in brands_upper for b in ['SIDE', 'SXS', 'UTV']) else ''
                
                data_row = {
                    '数据时间': datetime.now().strftime('%m/%d/%Y'),
                    '计数': 'Y',
                    '品牌': 'CF Moto',
                    '网址': dealer_url,
                    '地址': address,
                    '邮编': zipcode,
                    '州': st,
                    '经销商名称': name,
                    '城市名称': '', # Often merged into address. Could extract via detailed reverse geocoding if imperative.
                    '状态': '',
                    'RZR': '',           # Polaris specific
                    'GENERAL': '',       # Polaris specific
                    'RANGER': '',        # Polaris specific
                    'SportsmanandACE': '', # Polaris specific
                    'Motorcycle': has_motorcycle,
                    'ATV': has_atv,
                    'Side x Side': has_sxs
                }
                
                results.append(data_row)
                
            # Autosave periodically
            pd.DataFrame(results, columns=COLUMNS).to_excel(output_path, index=False)
            
    except KeyboardInterrupt:
        logger.warning("Aborted manually. Saving latest dataframe...")
    except Exception as e:
        logger.error(f"Critical error during execution: {e}")
    finally:
        if results:
            df = pd.DataFrame(results, columns=COLUMNS)
            df.to_excel(output_path, index=False)
            logger.info(f"DONE! CFmotoUSA successfully saved {len(results)} distinct dealers to {output_path}")
        else:
            logger.warning("No dealers successfully gathered.")
            
        driver.quit()

if __name__ == "__main__":
    scrape_cfmoto()
